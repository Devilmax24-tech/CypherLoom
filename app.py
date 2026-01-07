import os
import io
import json
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, abort, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from sqlalchemy import desc, or_
from urllib.parse import urlencode

from config import Config

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = 'your-secret-key'

UPLOAD_FOLDER = 'temp_uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'jpg', 'jpeg', 'png', 'gif', 'pptx', 'zip'}
MAX_FILE_SIZE = 50 * 1024 * 1024

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Models (Keep your existing models)
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100))
    profile_pic = db.Column(db.String(200), default='default.jpg')
    bio = db.Column(db.Text)
    year = db.Column(db.String(20))
    branch = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    last_login = db.Column(db.DateTime)
    
    progress = db.relationship('Progress', backref='user', lazy=True)

class Resource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    file_id = db.Column(db.String(500))
    file_name = db.Column(db.String(300))
    file_type = db.Column(db.String(50))
    file_size = db.Column(db.String(20))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    resource_type = db.Column(db.String(50))  # notes, pyq, sample_paper, books
    year = db.Column(db.Integer)
    semester = db.Column(db.String(10))
    branch = db.Column(db.String(50))
    subject = db.Column(db.String(100))
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    downloads = db.Column(db.Integer, default=0)
    views = db.Column(db.Integer, default=0)
    tags = db.Column(db.String(500))
    is_approved = db.Column(db.Boolean, default=True)
    rating = db.Column(db.Float, default=0.0)
    
    uploader = db.relationship('User', backref='uploaded_resources')

class Progress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'))
    subject = db.Column(db.String(100), nullable=False)
    topic = db.Column(db.String(200))
    completed = db.Column(db.Boolean, default=False)
    score = db.Column(db.Float)
    date_completed = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    time_spent = db.Column(db.Integer, default=0)  # in minutes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Google Drive Service
def get_drive_service():
    try:
        if not app.config.get('SERVICE_ACCOUNT_FILE'):
            flash('Google Drive service account not configured', 'error')
            return None
            
        creds = service_account.Credentials.from_service_account_file(
            app.config['SERVICE_ACCOUNT_FILE'],
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)
        return service
    except Exception as e:
        app.logger.error(f"Drive service error: {e}")
        flash(f'Google Drive service error: {str(e)}', 'error')
        return None

# Get or create folder ID for branch
def get_or_create_folder(service, branch_name):
    try:
        # First, check if folder already exists
        query = f"name='{branch_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        folders = results.get('files', [])
        
        if folders:
            return folders[0]['id']
        
        # Create new folder if it doesn't exist
        folder_metadata = {
            'name': branch_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        folder = service.files().create(body=folder_metadata, fields='id').execute()
        return folder.get('id')
        
    except Exception as e:
        app.logger.error(f"Folder creation error: {e}")
        return None

# Upload file to Google Drive in specific branch folder
def upload_to_drive(service, file_path, file_name, branch_name):
    try:
        # Get or create folder for the branch
        folder_id = get_or_create_folder(service, branch_name)
        if not folder_id:
            return None, "Failed to create/get branch folder"
        
        # Prepare file metadata
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        
        # Upload file
        media = MediaFileUpload(
            file_path,
            mimetype='application/pdf' if file_name.lower().endswith('.pdf') else 'application/octet-stream',
            resumable=True
        )
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name, webViewLink'
        ).execute()
        
        return file.get('id'), None
        
    except Exception as e:
        app.logger.error(f"Upload to Drive error: {e}")
        return None, str(e)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.before_request
def before_request():
    if current_user.is_authenticated:
        current_user.last_login = datetime.now(timezone.utc)
        db.session.commit()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Routes
@app.route('/')
def index():
    featured = Resource.query.filter_by(is_approved=True).order_by(desc(Resource.downloads)).limit(6).all()
    
    stats = {
        'total_resources': Resource.query.filter_by(is_approved=True).count(),
        'total_users': User.query.count(),
        'total_downloads': db.session.query(db.func.sum(Resource.downloads)).scalar() or 0,
        'recent_uploads': Resource.query.filter_by(is_approved=True).order_by(desc(Resource.upload_date)).limit(5).all()
    }
    
    return render_template('index.html', 
                          featured=featured, 
                          stats=stats)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form.get('confirm_password', '')
        year = request.form['year']
        branch = request.form['branch']
        
        if len(password) < 5:
            flash('Password must be at least 5 characters', 'danger')
            return redirect(url_for('register'))
        
        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('register'))
        
        user = User.query.filter((User.username == username) | (User.email == email)).first()
        if user:
            flash('Username or email already exists', 'danger')
            return redirect(url_for('register'))
        
        hashed_password = generate_password_hash(password)
        new_user = User(
            username=username,
            email=email,
            password_hash=hashed_password,
            year=year,
            branch=branch
        )
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = 'remember' in request.form
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            flash('Login successful!', 'success')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password!', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    total_resources = Resource.query.filter_by(is_approved=True).count()
    user_uploads = Resource.query.filter_by(uploader_id=current_user.id).count()
    completed_progress = Progress.query.filter_by(user_id=current_user.id, completed=True).count()
    
    recent_uploads = Resource.query.filter_by(uploader_id=current_user.id)\
        .order_by(desc(Resource.upload_date)).limit(5).all()
    
    recent_progress = Progress.query.filter_by(user_id=current_user.id)\
        .order_by(desc(Progress.created_at)).limit(5).all()
    
    recommended = Resource.query.filter_by(branch=current_user.branch, is_approved=True)\
        .order_by(desc(Resource.rating)).limit(5).all()
    
    return render_template('dashboard.html',
                         total_resources=total_resources,
                         user_uploads=user_uploads,
                         completed_progress=completed_progress,
                         recent_uploads=recent_uploads,
                         recent_progress=recent_progress,
                         recommended=recommended)

@app.context_processor
def utility_processor():
    def update_sort_url(sort_value):
        args = request.args.copy()
        args['sort'] = sort_value
        if 'page' in args:
            del args['page']
        return f'/resources?{urlencode(args)}'
    
    def update_page_url(page_num):
        args = request.args.copy()
        args['page'] = page_num
        return f'/resources?{urlencode(args)}'
    
    def get_page_url(page_num):
        return update_page_url(page_num)
    
    return dict(
        update_sort_url=update_sort_url,
        update_page_url=update_page_url,
        get_page_url=get_page_url
    )

@app.route('/resources')
@login_required
def resources():
    search = request.args.get('search', '').strip()
    resource_type = request.args.get('type', '')
    branch = request.args.get('branch', '')
    semester = request.args.get('semester', '')
    year_filter = request.args.get('year', '')
    subject = request.args.get('subject', '')
    sort_by = request.args.get('sort', 'recent')
    
    query = Resource.query.filter_by(is_approved=True)
    
    if search:
        query = query.filter(
            or_(
                Resource.title.ilike(f'%{search}%'),
                Resource.description.ilike(f'%{search}%'),
                Resource.subject.ilike(f'%{search}%'),
                Resource.tags.ilike(f'%{search}%')
            )
        )
    
    if resource_type:
        query = query.filter_by(resource_type=resource_type)
    
    if branch:
        query = query.filter_by(branch=branch)
    
    if semester:
        query = query.filter_by(semester=semester)
    
    if year_filter:
        query = query.filter_by(year=year_filter)
    
    if subject:
        query = query.filter_by(subject=subject)
    
    if sort_by == 'downloads':
        query = query.order_by(desc(Resource.downloads))
    elif sort_by == 'views':
        query = query.order_by(desc(Resource.views))
    elif sort_by == 'rating':
        query = query.order_by(desc(Resource.rating))
    else:
        query = query.order_by(desc(Resource.upload_date))
    
    page = request.args.get('page', 1, type=int)
    per_page = 12
    resources_paginated = query.paginate(page=page, per_page=per_page, error_out=False)
    
    branches = db.session.query(Resource.branch).distinct().filter(Resource.branch.isnot(None)).all()
    semesters = db.session.query(Resource.semester).distinct().filter(Resource.semester.isnot(None)).all()
    subjects = db.session.query(Resource.subject).distinct().filter(Resource.subject.isnot(None)).all()
    years = db.session.query(Resource.year).distinct().filter(Resource.year.isnot(None)).order_by(desc(Resource.year)).all()
    
    return render_template('resources.html',
                         resources=resources_paginated,
                         search=search,
                         resource_type=resource_type,
                         branch_filter=branch,
                         semester_filter=semester,
                         sort_by=sort_by,
                         year_filter=year_filter,
                         subject_filter=subject,
                         branches=[b[0] for b in branches],
                         semesters=[s[0] for s in semesters],
                         subjects=[s[0] for s in subjects],
                         years=[y[0] for y in years])

@app.route('/resource/<int:resource_id>')
@login_required
def view_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    
    if not resource.is_approved:
        abort(404)
    
    resource.views += 1
    db.session.commit()
    
    related_resources = Resource.query.filter(
        Resource.id != resource.id,
        Resource.is_approved == True,
        Resource.subject == resource.subject
    ).limit(4).all()
    
    return render_template('view_resource.html',
                         resource=resource,
                         related_resources=related_resources)

@app.route('/download/<int:resource_id>')
@login_required
def download_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    
    if not resource.is_approved:
        abort(404)
    
    try:
        service = get_drive_service()
        if service and resource.file_id:
            request = service.files().get_media(fileId=resource.file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            
            fh.seek(0)
            
            resource.downloads += 1
            db.session.commit()
            
            try:
                progress = Progress(
                    user_id=current_user.id,
                    resource_id=resource.id,
                    subject=resource.subject,
                    topic=f"Downloaded: {resource.title}",
                    date_completed=datetime.utcnow()
                )
                db.session.add(progress)
                db.session.commit()
            except:
                db.session.rollback()
            
            return send_file(
                fh,
                as_attachment=True,
                download_name=resource.file_name,
                mimetype='application/octet-stream'
            )
        else:
            flash('File not available for download.', 'warning')
            return redirect(url_for('view_resource', resource_id=resource_id))
            
    except Exception as e:
        app.logger.error(f"Download error: {e}")
        flash('Error downloading file. Please try again.', 'danger')
        return redirect(url_for('view_resource', resource_id=resource_id))

@app.route('/preview/<int:resource_id>')
@login_required
def preview_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)

    if not resource.is_approved:
        abort(404)
    
    if not resource.file_id:
        error_html = """
        <html>
            <body style="display: flex; justify-content: center; align-items: center; height: 100vh; background: #f8f9fa;">
                <div class="text-center p-4">
                    <i class="fas fa-exclamation-triangle fa-3x text-warning mb-3"></i>
                    <h5 class="text-danger">File Not Available</h5>
                    <p class="text-muted">This resource doesn't have an attached file.</p>
                </div>
            </body>
        </html>
        """
        return make_response(error_html, 404)

    try:
        service = get_drive_service()
        if not service:
            abort(404)

        request = service.files().get_media(fileId=resource.file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()

        fh.seek(0)
        pdf_bytes = fh.read()

        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename="{resource.file_name}"'
        return response

    except Exception as e:
        app.logger.error(f"Preview error: {e}")
        abort(404)

@app.route('/progress')
@login_required
def progress():
    user_progress = Progress.query.filter_by(user_id=current_user.id).order_by(Progress.created_at.desc()).all()
    
    total_items = len(user_progress)
    completed_items = sum(1 for item in user_progress if item.completed)
    completion_rate = (completed_items / total_items * 100) if total_items > 0 else 0
    
    return render_template('progress.html',
                         progress=user_progress,
                         total_items=total_items,
                         completed_items=completed_items,
                         completion_rate=completion_rate)

@app.route('/mark_complete/<int:progress_id>', methods=['POST'])
@login_required
def mark_complete(progress_id):
    progress_item = Progress.query.get_or_404(progress_id)
    
    if progress_item.user_id != current_user.id:
        abort(403)
    
    progress_item.completed = not progress_item.completed
    db.session.commit()
    
    flash('Progress updated!', 'success')
    return redirect(url_for('progress'))

@app.route('/add_progress', methods=['POST'])
@login_required
def add_progress():
    try:
        subject = request.form['subject']
        topic = request.form['topic']
        notes = request.form.get('notes', '')
        
        new_progress = Progress(
            user_id=current_user.id,
            subject=subject,
            topic=topic,
            notes=notes,
            completed=False
        )
        
        db.session.add(new_progress)
        db.session.commit()
        
        flash('Progress added successfully!', 'success')
        return redirect(url_for('progress'))
    
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding progress: {str(e)}', 'danger')
        return redirect(url_for('progress'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        try:
            current_user.full_name = request.form.get('full_name', current_user.full_name)
            current_user.email = request.form.get('email', current_user.email)
            current_user.bio = request.form.get('bio', current_user.bio)
            current_user.year = request.form.get('year', current_user.year)
            current_user.branch = request.form.get('branch', current_user.branch)
            current_user.updated_at = datetime.now(timezone.utc)
            
            if 'profile_pic' in request.files:
                file = request.files['profile_pic']
                
                if file and file.filename != '':
                    file.seek(0, os.SEEK_END)
                    file_length = file.tell()
                    file.seek(0)
                    
                    if file_length > MAX_FILE_SIZE:
                        flash('File size too large (max 2MB)', 'danger')
                        return redirect(url_for('profile'))
                    
                    if allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        unique_filename = f"{current_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
                        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
                        
                        file.save(filepath)
                        
                        if current_user.profile_pic and current_user.profile_pic != 'default.jpg':
                            old_filepath = os.path.join(UPLOAD_FOLDER, current_user.profile_pic)
                            if os.path.exists(old_filepath):
                                os.remove(old_filepath)
                        
                        current_user.profile_pic = unique_filename
                    else:
                        flash('Allowed file types: png, jpg, jpeg, gif', 'danger')
                        return redirect(url_for('profile'))
            
            db.session.commit()
            flash('Profile updated successfully!', 'success')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating profile: {str(e)}', 'danger')
        
        return redirect(url_for('profile'))
    
    user_progress = Progress.query.filter_by(user_id=current_user.id).all()
    
    subject_stats = {}
    for item in user_progress:
        if item.subject not in subject_stats:
            subject_stats[item.subject] = {
                'total': 0,
                'completed': 0,
                'completion_rate': 0
            }
        subject_stats[item.subject]['total'] += 1
        if item.completed:
            subject_stats[item.subject]['completed'] += 1
    
    for subject in subject_stats:
        stats = subject_stats[subject]
        if stats['total'] > 0:
            stats['completion_rate'] = (stats['completed'] / stats['total']) * 100
    
    recent_progress = []
    if user_progress:
        sorted_progress = sorted(
            user_progress, 
            key=lambda x: x.created_at if x.created_at else datetime.min.replace(tzinfo=timezone.utc), 
            reverse=True
        )
        recent_progress = sorted_progress[:5]
    
    return render_template('profile.html', 
                         user=current_user,
                         subject_stats=subject_stats,
                         recent_progress=recent_progress)

@app.route('/api/search_suggestions')
@login_required
def search_suggestions():
    query = request.args.get('q', '').lower()
    if len(query) < 2:
        return jsonify([])
    
    resources = Resource.query.filter(
        Resource.title.ilike(f'%{query}%') |
        Resource.subject.ilike(f'%{query}%')
    ).limit(10).all()
    
    suggestions = [{
        'id': r.id,
        'title': r.title,
        'subject': r.subject,
        'type': r.resource_type
    } for r in resources]
    
    return jsonify(suggestions)

@app.route('/api/recent_uploads')
@login_required
def recent_uploads_api():
    uploads = Resource.query.filter_by(is_approved=True)\
        .order_by(desc(Resource.upload_date))\
        .limit(5)\
        .all()
    
    data = [{
        'id': r.id,
        'title': r.title,
        'type': r.resource_type,
        'subject': r.subject,
        'date': r.upload_date.strftime('%b %d'),
        'downloads': r.downloads
    } for r in uploads]
    
    return jsonify(data)

# ================= ADMIN ROUTES =================
ADMIN_SECRET_PATH = "901c5d592a1e3dc872a2b8da35a2a60442abbddb59a1a43f8f313b8eb814d537"

@app.route(f'/{ADMIN_SECRET_PATH}', methods=['GET', 'POST'])
@login_required
def admin_portal():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    
    # Get statistics - MATCHING YOUR upload.html TEMPLATE
    total_users = User.query.count()
    total_files = Resource.query.count()
    
    # Calculate total size (from Google Drive - estimate or skip)
    total_size_mb = 0  # You can't calculate this easily from Google Drive
    
    # Get all users (except current admin)
    users = User.query.filter(User.id != current_user.id).all()
    
    # Get all files from database (Resources)
    all_files = Resource.query.order_by(desc(Resource.upload_date)).limit(50).all()
    
    # Prepare file sizes and dates
    file_sizes = {}
    file_dates = {}
    file_downloads = {}
    
    for resource in all_files:
        file_sizes[resource.file_name] = resource.file_size
        file_dates[resource.file_name] = resource.upload_date.strftime('%Y-%m-%d %H:%M')
        file_downloads[resource.file_name] = resource.downloads
    
    current_year = datetime.now().year
    
    return render_template('upload.html',
                         stats={
                             'total_users': total_users,
                             'total_files': total_files,
                             'total_size_mb': total_size_mb,
                             'today_uploads': 0
                         },
                         users=users,
                         all_files=[r.file_name for r in all_files],
                         file_sizes=file_sizes,
                         file_dates=file_dates,
                         file_downloads=file_downloads,
                         current_year=current_year)

@app.route('/admin/upload', methods=['POST'])
@login_required
def admin_upload():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    
    if 'files' not in request.files:
        flash('No files selected', 'error')
        return redirect(f'/{ADMIN_SECRET_PATH}')
    
    files = request.files.getlist('files')
    uploaded_count = 0
    
    for file in files:
        if file.filename == '':
            continue
        
        if file and allowed_file(file.filename):
            try:
                # Save file temporarily
                filename = secure_filename(file.filename)
                temp_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(temp_path)
                
                # Get form data
                branch = request.form.get('branch', 'General')
                semester = request.form.get('semester', '1')
                resource_type = request.form.get('category', 'notes')
                subject = request.form.get('subject', 'General')
                description = request.form.get('description', '')
                year = request.form.get('year', datetime.now().year)
                title = request.form.get('title', filename)
                
                # Get Google Drive service
                service = get_drive_service()
                if not service:
                    flash('Google Drive service unavailable', 'error')
                    os.remove(temp_path)
                    return redirect(f'/{ADMIN_SECRET_PATH}')
                
                # Upload to Google Drive in branch folder
                file_id, error = upload_to_drive(service, temp_path, filename, branch)
                
                if error:
                    flash(f'Failed to upload {filename} to Google Drive: {error}', 'error')
                    os.remove(temp_path)
                    continue
                
                # Get file size
                file_size = os.path.getsize(temp_path)
                
                # Create Resource record
                resource = Resource(
                    title=title,
                    description=description,
                    file_id=file_id,  # This is the Google Drive file ID
                    file_name=filename,
                    file_type=file.content_type,
                    file_size=str(file_size),
                    resource_type=resource_type,
                    year=int(year) if year else datetime.now().year,
                    semester=semester,
                    branch=branch,
                    subject=subject,
                    uploader_id=current_user.id,
                    is_approved=True,
                    upload_date=datetime.utcnow(),
                    downloads=0,
                    views=0
                )
                
                db.session.add(resource)
                uploaded_count += 1
                
                # Delete temp file
                os.remove(temp_path)
                
            except Exception as e:
                app.logger.error(f"Upload error for {file.filename}: {e}")
                flash(f'Error uploading {file.filename}: {str(e)}', 'error')
                continue
    
    if uploaded_count > 0:
        db.session.commit()
        flash(f'Successfully uploaded {uploaded_count} file(s) to Google Drive!', 'success')
    else:
        flash('No files were uploaded', 'error')
    
    return redirect(f'/{ADMIN_SECRET_PATH}')

@app.route('/admin/delete/user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        return redirect(url_for('index'))
    
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id or user.is_admin:
        flash('Cannot delete admin users', 'error')
        return redirect(f'/{ADMIN_SECRET_PATH}')
    
    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.username} deleted', 'success')
    return redirect(f'/{ADMIN_SECRET_PATH}')

@app.route('/admin/delete/resource/<int:resource_id>', methods=['POST'])
@login_required
def delete_resource(resource_id):
    if not current_user.is_admin:
        return redirect(url_for('index'))
    
    resource = Resource.query.get_or_404(resource_id)
    
    try:
        # Optional: Delete from Google Drive too
        service = get_drive_service()
        if service and resource.file_id:
            try:
                service.files().delete(fileId=resource.file_id).execute()
            except:
                pass  # Continue even if Drive delete fails
        
        db.session.delete(resource)
        db.session.commit()
        flash(f'Resource {resource.title} deleted', 'success')
    except Exception as e:
        flash(f'Error deleting resource: {str(e)}', 'error')
    
    return redirect(f'/{ADMIN_SECRET_PATH}')

# Initialize database
with app.app_context():
    db.create_all()
    
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            email='admin@cypherloom.com',
            password_hash=generate_password_hash('admin123'),
            full_name='Administrator',
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)