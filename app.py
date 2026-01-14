import os
import io
import json
import pickle
import uuid
import shutil
from datetime import datetime, timezone
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, abort, make_response, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

from sqlalchemy import desc, or_, func, text, inspect

from config import Config

# ================== FLASK APP SETUP ==================

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config.get('SECRET_KEY', 'your-secret-key-change-in-production')

# ================== CONFIGURATION ==================

UPLOAD_FOLDER = 'temp_uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'jpg', 'jpeg', 'png', 'gif', 'pptx', 'zip'}
MAX_FILE_SIZE = 50 * 1024 * 1024

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Create local storage folder as fallback
LOCAL_STORAGE = 'local_storage'
os.makedirs(LOCAL_STORAGE, exist_ok=True)
print(f"✅ Local storage folder: {LOCAL_STORAGE}")

# ================== DATABASE SETUP ==================

db = SQLAlchemy(app)
login_manager = LoginManager() 
login_manager.init_app(app)
login_manager.login_view = 'login'

# ================== GOOGLE DRIVE OAUTH CONFIG ==================

# OAuth 2.0 Configuration
SCOPES = ['https://www.googleapis.com/auth/drive.file']
CLIENT_SECRETS_FILE = 'client_secrets.json'
TOKEN_FILE = 'token.pickle'

# ================== MODELS ==================

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
    
    progress = db.relationship('Progress', backref='user',lazy=True)

class Resource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    file_id = db.Column(db.String(500))
    file_name = db.Column(db.String(300))
    file_type = db.Column(db.String(50))
    file_size = db.Column(db.String(20))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    resource_type = db.Column(db.String(50))
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
    drive_url = db.Column(db.String(500))
    
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
    time_spent = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ================== GOOGLE DRIVE FUNCTIONS ==================

# def get_drive_service():
#     """Get Google Drive service using OAuth 2.0"""
#     creds = None
    
#     # Load credentials from token file
#     if os.path.exists(TOKEN_FILE):
#         with open(TOKEN_FILE, 'rb') as token:
#             creds = pickle.load(token)
    
#     # If no valid credentials, return None
#     if not creds or not creds.valid:
#         if creds and creds.expired and creds.refresh_token:
#             try:
#                 creds.refresh(Request())
#                 # Save refreshed credentials
#                 with open(TOKEN_FILE, 'wb') as token:
#                     pickle.dump(creds, token)
#                 print("✅ Google Drive credentials refreshed")
#             except Exception as e:
#                 print(f"❌ Failed to refresh credentials: {e}")
#                 return None
#         else:
#             return None  # User needs to authorize
    
#     return build('drive', 'v3', credentials=creds)

def get_drive_service():
    """Get Google Drive service using OAuth 2.0"""
    # Check if we're on Render without a token file
    if os.environ.get('RENDER') and not os.path.exists(TOKEN_FILE):
        print("🚨 RENDER: No Google Drive token found, uploads will fail")
        return None
    
    creds = None
    
    # Load credentials from token file
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)
    
    # If no valid credentials, return None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Save refreshed credentials
                with open(TOKEN_FILE, 'wb') as token:
                    pickle.dump(creds, token)
                print("✅ Google Drive credentials refreshed")
            except Exception as e:
                print(f"❌ Failed to refresh credentials: {e}")
                return None
        else:
            return None  # User needs to authorize
    
    return build('drive', 'v3', credentials=creds)

def upload_to_drive(service, file_path, file_name, branch_name):
    """Upload file to Google Drive and return file_id and public URL"""
    try:
        print(f"=== GOOGLE DRIVE UPLOAD: {file_name} ===")
        # FIX: Use actual branch name, not 'General' as default
        if not branch_name or branch_name.strip() == '':
            branch_name = 'General'  # Default if empty
        else:
            branch_name = branch_name.strip()
        
        print(f"📂 Using folder: '{branch_name}'")

        # Create or find folder
        folder_id = get_or_create_folder(service, branch_name)
        if not folder_id:
            return None, None, "Failed to create/get folder in Google Drive"
        
        print(f"✅ Folder ID: {folder_id}")
        
        # File metadata
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        
        # Determine MIME type
        if file_name.lower().endswith('.pdf'):
            mime_type = 'application/pdf'
        elif file_name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
            mime_type = f'image/{file_name.split(".")[-1].lower()}'
        else:
            mime_type = 'application/octet-stream'
        
        media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
        
        # Upload file
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name, webViewLink'
        ).execute()
        
        file_id = file.get('id')
        web_view_link = file.get('webViewLink')
        
        print(f"✅ Uploaded to Google Drive! File ID: {file_id}")
        
        # Make file publicly accessible
        try:
            permission = {
                'type': 'anyone',
                'role': 'reader'
            }
            service.permissions().create(
                fileId=file_id,
                body=permission,
                fields='id'
            ).execute()
            
            # Create direct download link
            drive_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            print(f"✅ File made public: {drive_url}")
            
        except Exception as e:
            print(f"⚠️ Could not set public permissions: {e}")
            drive_url = web_view_link if web_view_link else f"https://drive.google.com/file/d/{file_id}/view"
        
        return file_id, drive_url, None
        
    except Exception as e:
        print(f"❌ Google Drive upload error: {e}")
        return None, None, str(e)

def get_or_create_folder(service, branch_name):
    """Get or create folder in Google Drive"""
    try:
        # Search for existing folder
        query = f"name='{branch_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        folders = results.get('files', [])
        
        if folders:
            return folders[0]['id']
        
        # Create new folder
        folder_metadata = {
            'name': branch_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        
        folder = service.files().create(
            body=folder_metadata,
            fields='id'
        ).execute()
        
        print(f"✅ Created folder '{branch_name}' in Google Drive")
        return folder.get('id')
        
    except Exception as e:
        print(f"❌ Folder creation error: {e}")
        return None

# def upload_to_local_fallback(file_path, file_name, branch_name):
#     """Fallback to local storage if Google Drive fails"""
#     try:
#         print(f"=== LOCAL STORAGE FALLBACK: {file_name} ===")
        
#         # Create branch folder
#         safe_branch = branch_name.replace(' ', '_').replace('/', '_')
#         local_folder = os.path.join(LOCAL_STORAGE, safe_branch)
#         os.makedirs(local_folder, exist_ok=True)
        
#         # Generate unique filename
#         timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
#         unique_id = str(uuid.uuid4())[:6]
#         safe_filename = secure_filename(file_name)
#         saved_filename = f"{timestamp}_{unique_id}_{safe_filename}"
        
#         dest_path = os.path.join(local_folder, saved_filename)
        
#         # Copy file to local storage
#         shutil.copy2(file_path, dest_path)
        
#         # Generate identifiers
#         file_id = f"local_{unique_id}"
#         drive_url = f"/local_files/{safe_branch}/{saved_filename}"
        
#         print(f"✅ File stored locally at: {dest_path}")
#         print(f"   Access URL: {drive_url}")
        
#         return file_id, drive_url, None
        
#     except Exception as e:
#         print(f"❌ Local storage error: {e}")
#         return None, None, str(e)

# ================== OAUTH AUTHORIZATION ROUTES ==================

@app.route('/authorize')
@login_required
def authorize():
    """Simple working Google Drive OAuth"""
    if not current_user.is_admin:
        flash('Only admins can authorize Google Drive', 'error')
        return redirect(url_for('admin_portal'))
    
    try:
        # Use simple manual OAuth URL
        import json
        
        # Load client secrets
        with open('client_secrets.json', 'r') as f:
            client_data = json.load(f)
        
        client_id = client_data['web']['client_id']
        
        # Build authorization URL manually
        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={client_id}&"
            "response_type=code&"
            "scope=https://www.googleapis.com/auth/drive.file&"
            f"redirect_uri={url_for('oauth2callback', _external=True)}&"
            "access_type=offline&"
            "prompt=consent"
        )
        
        return redirect(auth_url)
        
    except FileNotFoundError:
        flash('client_secrets.json file not found!', 'error')
        return redirect(url_for('admin_portal'))
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('admin_portal'))

@app.route('/oauth2callback')
def oauth2callback():
    """Simple OAuth callback"""
    try:
        code = request.args.get('code')
        
        if not code:
            flash('No authorization code received', 'error')
            return redirect(url_for('admin_portal'))
        
        # Exchange code for tokens
        import json
        import requests
        
        # Load client secrets
        with open('client_secrets.json', 'r') as f:
            client_data = json.load(f)
        
        # Prepare token request
        token_url = "https://oauth2.googleapis.com/token"
        data = {
            'code': code,
            'client_id': client_data['web']['client_id'],
            'client_secret': client_data['web']['client_secret'],
            'redirect_uri': url_for('oauth2callback', _external=True),
            'grant_type': 'authorization_code'
        }
        
        # Get tokens
        response = requests.post(token_url, data=data)
        token_info = response.json()
        
        if 'error' in token_info:
            flash(f'Error: {token_info["error_description"]}', 'error')
            return redirect(url_for('admin_portal'))
        
        # Create credentials object
        from google.oauth2.credentials import Credentials
        
        creds = Credentials(
            token=token_info['access_token'],
            refresh_token=token_info.get('refresh_token'),
            token_uri=token_info.get('token_uri', 'https://oauth2.googleapis.com/token'),
            client_id=client_data['web']['client_id'],
            client_secret=client_data['web']['client_secret'],
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        
        # Save credentials
        import pickle
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
        
        flash('✅ Google Drive connected successfully!', 'success')
        return redirect(url_for('admin_portal'))
        
    except Exception as e:
        flash(f'Callback error: {str(e)}', 'error')
        return redirect(url_for('admin_portal'))

@app.route('/drive-status')
@login_required
def drive_status():
    """Check Google Drive connection status"""
    service = get_drive_service()
    
    # Debug: Check if token file exists
    token_exists = os.path.exists('token.pickle')
    debug_info = f"Token file exists: {token_exists}"
    
    if service:
        try:
            # Test connection
            about = service.about().get(fields="user").execute()
            user_email = about.get('user', {}).get('emailAddress', 'Unknown')
            
            return f'''
            <div class="alert alert-success">
                <strong>✅ Google Drive Connected</strong><br>
                Account: {user_email}<br>
                Status: Ready for uploads<br>
                <small class="text-muted">{debug_info}</small>
            </div>
            '''
        except Exception as e:
            return f'''
            <div class="alert alert-warning">
                <strong>⚠️ Google Drive Error</strong><br>
                Error: {str(e)}<br>
                Debug: {debug_info}<br>
                <a href="/authorize" class="btn btn-sm btn-primary mt-2">Re-authorize</a>
            </div>
            '''
    
    return f'''
    <div class="alert alert-warning">
        <strong>⚠️ Google Drive Not Connected</strong><br>
        {debug_info}<br>
        Please authorize Google Drive to upload files.<br>
        <a href="/authorize" class="btn btn-sm btn-primary mt-2">Connect Google Drive</a>
    </div>
    '''

# ================== LOCAL FILE SERVING ==================

@app.route('/local_files/<path:filename>')
@login_required
def serve_local_file(filename):
    """Serve files from local storage (fallback)"""
    try:
        safe_path = os.path.join(LOCAL_STORAGE, filename)
        absolute_path = os.path.abspath(safe_path)
        
        # Security check
        if not absolute_path.startswith(os.path.abspath(LOCAL_STORAGE)):
            abort(403)
        
        directory = os.path.dirname(safe_path)
        file_name = os.path.basename(safe_path)
        
        return send_from_directory(directory, file_name, as_attachment=True)
        
    except Exception as e:
        print(f"Local file serve error: {e}")
        abort(404)

# ================== LOGIN MANAGER ==================

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

# ================== MAIN ROUTES ==================

@app.route('/')
def index():
    featured = Resource.query.filter_by(is_approved=True).order_by(desc(Resource.downloads)).limit(6).all()
    
    stats = {
        'total_resources': Resource.query.filter_by(is_approved=True).count(),
        'total_users': User.query.count(),
        'total_downloads': db.session.query(func.sum(Resource.downloads)).scalar() or 0,
        'recent_uploads': Resource.query.filter_by(is_approved=True).order_by(desc(Resource.upload_date)).limit(5).all()
    }
    
    return render_template('index.html', featured=featured, stats=stats)

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
    user_uploads = Resource.query.filter_by(uploader_id=current_user.id).count()
    total_resources = Resource.query.filter_by(is_approved=True).count()
    completed_progress = Progress.query.filter_by(user_id=current_user.id, completed=True).count()
    recent_resources = Resource.query.filter_by(is_approved=True).order_by(desc(Resource.upload_date)).limit(5).all()
    recommended_resources = Resource.query.filter(Resource.branch == current_user.branch, Resource.is_approved == True).order_by(desc(Resource.rating)).limit(5).all()
    recent_user_progress = Progress.query.filter_by(user_id=current_user.id).order_by(desc(Progress.created_at)).limit(5).all()
    total_downloads = db.session.query(func.sum(Resource.downloads)).scalar() or 0
    total_views = db.session.query(func.sum(Resource.views)).scalar() or 0
    
    today = datetime.now().date()
    today_uploads = Resource.query.filter(Resource.uploader_id == current_user.id, func.date(Resource.upload_date) == today).count()
    
    return render_template('dashboard.html',
                         total_resources=total_resources,
                         user_uploads=user_uploads,
                         completed_progress=completed_progress,
                         recent_resources=recent_resources,
                         recommended_resources=recommended_resources,
                         recent_user_progress=recent_user_progress,
                         total_downloads=total_downloads,
                         total_views=total_views,
                         today_uploads=today_uploads)

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
    
    return dict(update_sort_url=update_sort_url, update_page_url=update_page_url)


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
            
            if 'profile_pic' in request.files:
                file = request.files['profile_pic']
                if file and file.filename != '':
                    if allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        unique_filename = f"{current_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
                        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
                        file.save(filepath)
                        current_user.profile_pic = unique_filename
            
            db.session.commit()
            flash('Profile updated successfully!', 'success')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating profile: {str(e)}', 'danger')
        
        return redirect(url_for('profile'))
    
    return render_template('profile.html', user=current_user)

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
    """Mark a progress item as complete"""
    progress = Progress.query.get_or_404(progress_id)
    
    # Check if the progress belongs to the current user
    if progress.user_id != current_user.id:
        abort(403)
    
    progress.completed = True
    progress.date_completed = datetime.utcnow()
    db.session.commit()
    
    flash('Progress marked as complete!', 'success')
    return redirect(url_for('progress'))

@app.route('/add_progress', methods=['POST'])
@login_required
def add_progress():
    """Add a new progress entry"""
    subject = request.form.get('subject', '').strip()
    topic = request.form.get('topic', '').strip()
    
    if not subject:
        flash('Subject is required!', 'danger')
        return redirect(url_for('progress'))
    
    progress = Progress(
        user_id=current_user.id,
        subject=subject,
        topic=topic if topic else None,
        completed=False,
        created_at=datetime.utcnow()
    )
    
    db.session.add(progress)
    db.session.commit()
    
    flash('Progress entry added successfully!', 'success')
    return redirect(url_for('progress'))

@app.route('/resources')
@login_required
def resources():
    search = request.args.get('search', '').strip()
    resource_type = request.args.get('resource_type', '')
    branch = request.args.get('branch', '')
    semester = request.args.get('semester', '')
    year_filter = request.args.get('year', '')
    subject = request.args.get('subject', '')
    sort_by = request.args.get('sort', 'recent')
    
    query = Resource.query.filter_by(is_approved=True)
    
    if search:
        query = query.filter(or_(
            Resource.title.ilike(f'%{search}%'),
            Resource.description.ilike(f'%{search}%'),
            Resource.subject.ilike(f'%{search}%'),
            Resource.tags.ilike(f'%{search}%')
        ))
    
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
    
    branches = db.session.query(Resource.branch).distinct().filter(Resource.branch.isnot(None), Resource.branch != '').order_by(Resource.branch).all()
    semesters = db.session.query(Resource.semester).distinct().filter(Resource.semester.isnot(None), Resource.semester != '').order_by(Resource.semester).all()
    subjects = db.session.query(Resource.subject).distinct().filter(Resource.subject.isnot(None), Resource.subject != '').order_by(Resource.subject).all()
    years = db.session.query(Resource.year).distinct().filter(Resource.year.isnot(None)).order_by(desc(Resource.year)).all()
    types = db.session.query(Resource.resource_type).distinct().filter(Resource.resource_type.isnot(None), Resource.resource_type != '').order_by(Resource.resource_type).all()
    
    return render_template('resources.html',
                         resources=resources_paginated,
                         search=search,
                         resource_type=resource_type,
                         branch_filter=branch,
                         semester_filter=semester,
                         sort_by=sort_by,
                         year_filter=year_filter,
                         subject_filter=subject,
                         branches=[b[0] for b in branches if b[0]],
                         semesters=[s[0] for s in semesters if s[0]],
                         subjects=[s[0] for s in subjects if s[0]],
                         types=[t[0] for t in types if t[0]],
                         years=[y[0] for y in years if y[0]])

@app.route('/resource/<int:resource_id>')
@login_required
def view_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    
    if not resource.is_approved:
        abort(404)
    
    resource.views += 1
    db.session.commit()
    
    related_resources = Resource.query.filter(Resource.id != resource.id, Resource.is_approved == True, Resource.subject == resource.subject).limit(4).all()
    
    return render_template('view_resource.html', resource=resource, related_resources=related_resources)

@app.route('/download/<int:resource_id>')
@login_required
def download_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    
    if not resource.is_approved:
        abort(404)
    
    try:
        # Check if it's a local file
        if resource.drive_url and resource.drive_url.startswith('/local_files'):
            file_path = resource.drive_url.replace('/local_files/', '', 1)
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
            
            return redirect(url_for('serve_local_file', filename=file_path))
        
        # If Google Drive URL
        elif resource.drive_url:
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
            
            return redirect(resource.drive_url)
        
        else:
            flash('File not available for download.', 'warning')
            return redirect(url_for('view_resource', resource_id=resource_id))
            
    except Exception as e:
        app.logger.error(f"Download error: {e}")
        flash('Error downloading file. Please try again.', 'danger')
        return redirect(url_for('view_resource', resource_id=resource_id))

@app.route('/view/<int:resource_id>')
@login_required
def view_resource_direct(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    
    if not resource.is_approved:
        abort(404)
    
    resource.views += 1
    db.session.commit()
    
    if resource.drive_url and resource.drive_url.startswith('/local_files'):
        flash('Local file - downloading instead of viewing', 'info')
        return redirect(url_for('download_resource', resource_id=resource_id))
    
    elif resource.drive_url:
        if 'export=download' in resource.drive_url:
            file_id = resource.drive_url.split('id=')[-1]
            view_url = f"https://drive.google.com/file/d/{file_id}/view"
        else:
            view_url = resource.drive_url
        
        return redirect(view_url)
    elif resource.file_id:
        view_url = f"https://drive.google.com/file/d/{resource.file_id}/view"
        return redirect(view_url)
    else:
        flash('File not available for viewing.', 'warning')
        return redirect(url_for('view_resource', resource_id=resource_id))
    
# @app.route('/delete_resource/<int:resource_id>', methods=['POST'])
# @login_required
# def delete_resource(resource_id):
#     if not current_user.is_admin:
#         abort(403)
    
#     resource = Resource.query.get_or_404(resource_id)
    
#     try:
#         # Delete from database
#         db.session.delete(resource)
#         db.session.commit()
#         flash('Resource deleted successfully!', 'success')
#     except Exception as e:
#         db.session.rollback()
#         flash(f'Error deleting resource: {str(e)}', 'error')
    
#     return redirect(url_for('admin_portal'))
@app.route('/delete_resource/<int:resource_id>', methods=['POST'])
@login_required
def delete_resource(resource_id):
    if not current_user.is_admin:
        abort(403)
    
    resource = Resource.query.get_or_404(resource_id)
    
    try:
        # Use raw SQL to delete from progress table
        from sqlalchemy import text
        
        # First delete from progress table
        db.session.execute(
            text("DELETE FROM progress WHERE resource_id = :resource_id"),
            {"resource_id": resource_id}
        )
        
        # Now delete the resource
        db.session.delete(resource)
        db.session.commit()
        
        flash('Resource deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting resource: {str(e)}', 'error')
    
    return redirect(url_for('admin_portal'))


@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        abort(403)
    
    user = User.query.get_or_404(user_id)
    
    # Prevent deleting yourself
    if user.id == current_user.id:
        flash('You cannot delete your own account!', 'error')
        return redirect(url_for('admin_portal'))
    
    try:
        # Delete associated resources first if needed
        # Resource.query.filter_by(user_id=user_id).delete()
        
        db.session.delete(user)
        db.session.commit()
        flash(f'User {user.username} deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting user: {str(e)}', 'error')
    
    return redirect(url_for('admin_portal'))

@app.route('/clear_old_files', methods=['POST'])
@login_required
def clear_old_files():
    if not current_user.is_admin:
        abort(403)
    
    try:
        import os
        import time
        from datetime import datetime, timedelta
        
        # Get all files in upload folder
        upload_folder = app.config['UPLOAD_FOLDER']
        files_cleared = 0
        
        for filename in os.listdir(upload_folder):
            filepath = os.path.join(upload_folder, filename)
            
            # Skip directories
            if os.path.isdir(filepath):
                continue
                
            # Get file modification time
            file_mtime = os.path.getmtime(filepath)
            file_age = datetime.now() - datetime.fromtimestamp(file_mtime)
            
            # Delete files older than 24 hours
            if file_age > timedelta(hours=24):
                os.remove(filepath)
                files_cleared += 1
        
        flash(f'Cleared {files_cleared} old temporary files!', 'success')
        
    except Exception as e:
        flash(f'Error clearing files: {str(e)}', 'error')
    
    return redirect(url_for('admin_portal'))

@app.route('/export_users')
@login_required
def export_users():
    if not current_user.is_admin:
        abort(403)
    
    try:
        import csv
        import io
        
        # Get all users
        users = User.query.all()
        
        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['ID', 'Username', 'Email', 'Full Name', 'Year', 'Branch', 
                        'Admin Status', 'Created At', 'Last Login'])
        
        # Write user data
        for user in users:
            writer.writerow([
                user.id,
                user.username,
                user.email,
                user.full_name or '',
                user.year or '',
                user.branch or '',
                'Yes' if user.is_admin else 'No',
                user.created_at.strftime('%Y-%m-%d %H:%M:%S') if user.created_at else '',
                user.last_login.strftime('%Y-%m-%d %H:%M:%S') if user.last_login else ''
            ])
        
        # Create response with CSV file
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = 'attachment; filename=users_export.csv'
        response.headers['Content-type'] = 'text/csv'
        
        flash('Users exported successfully!', 'success')
        return response
        
    except Exception as e:
        flash(f'Error exporting users: {str(e)}', 'error')
        return redirect(url_for('admin_portal'))

@app.route('/system_backup', methods=['POST'])
@login_required
def system_backup():
    if not current_user.is_admin:
        abort(403)
    
    try:
        import json
        import csv
        import io
        from datetime import datetime
        
        # Create backup data
        backup_data = {
            'timestamp': datetime.now().isoformat(),
            'users': [],
            'resources': [],
            'progress': []
        }
        
        # Backup users
        users = User.query.all()
        for user in users:
            backup_data['users'].append({
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'full_name': user.full_name,
                'year': user.year,
                'branch': user.branch,
                'is_admin': user.is_admin,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'last_login': user.last_login.isoformat() if user.last_login else None
            })
        
        # Backup resources
        resources = Resource.query.all()
        for resource in resources:
            backup_data['resources'].append({
                'id': resource.id,
                'title': resource.title,
                'description': resource.description,
                'file_name': resource.file_name,
                'file_type': resource.file_type,
                'resource_type': resource.resource_type,
                'year': resource.year,
                'semester': resource.semester,
                'branch': resource.branch,
                'subject': resource.subject,
                'uploader_id': resource.uploader_id,
                'downloads': resource.downloads,
                'views': resource.views,
                'rating': resource.rating,
                'upload_date': resource.upload_date.isoformat() if resource.upload_date else None
            })
        
        # Backup progress
        progress_records = Progress.query.all()
        for progress in progress_records:
            backup_data['progress'].append({
                'id': progress.id,
                'user_id': progress.user_id,
                'resource_id': progress.resource_id,
                'subject': progress.subject,
                'topic': progress.topic,
                'completed': progress.completed,
                'score': progress.score,
                'date_completed': progress.date_completed.isoformat() if progress.date_completed else None,
                'created_at': progress.created_at.isoformat() if progress.created_at else None
            })
        
        # Create JSON backup file
        backup_json = json.dumps(backup_data, indent=2, default=str)
        
        # Create response with backup file
        response = make_response(backup_json)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        response.headers['Content-Disposition'] = f'attachment; filename=system_backup_{timestamp}.json'
        response.headers['Content-type'] = 'application/json'
        
        flash('System backup created successfully!', 'success')
        return response
        
    except Exception as e:
        flash(f'Error creating backup: {str(e)}', 'error')
        return redirect(url_for('admin_portal'))

# ================== ADMIN ROUTES ==================

ADMIN_SECRET_PATH = "901c5d592a1e3dc872a2b8da35a2a60442abbddb59a1a43f8f313b8eb814d537"

@app.route(f'/{ADMIN_SECRET_PATH}', methods=['GET', 'POST'])
@login_required
def admin_portal():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    
    total_users = User.query.count()
    all_resources = Resource.query.order_by(desc(Resource.upload_date)).all()
    total_resources = len(all_resources)
    
    total_size_mb = 0
    for resource in all_resources:
        if resource.file_size:
            try:
                size_str = str(resource.file_size)
                if 'MB' in size_str:
                    size_val = float(size_str.replace('MB', '').strip())
                    total_size_mb += size_val
                elif 'KB' in size_str:
                    size_val = float(size_str.replace('KB', '').strip())
                    total_size_mb += size_val / 1024
                elif 'Bytes' in size_str or size_str.isdigit():
                    if size_str.isdigit():
                        size_val = float(size_str)
                    else:
                        size_val = float(size_str.replace('Bytes', '').strip())
                    total_size_mb += size_val / (1024 * 1024)
            except:
                continue
    
    users = User.query.filter(User.id != current_user.id).all()
    current_year = datetime.now().year
    
    # Get drive status
    drive_status_html = drive_status()
    
    return render_template('upload.html',
                         stats={
                             'total_users': total_users,
                             'total_files': total_resources,
                             'total_size_mb': round(total_size_mb, 2),
                             'today_uploads': 0
                         },
                         users=users,
                         all_resources=all_resources[:50],
                         current_year=current_year,
                         drive_status=drive_status_html)

@app.route('/admin/upload', methods=['POST'])
@login_required
def admin_upload():
    print("🚀 ADMIN UPLOAD FUNCTION CALLED")
    
    if not current_user.is_admin:
        return redirect(url_for('index'))
    
    if 'files' not in request.files:
        flash('No files selected', 'error')
        return redirect(f'/{ADMIN_SECRET_PATH}')
    
    files = request.files.getlist('files')
    uploaded_count = 0
    errors = []
    
    # Get form data
    branch = request.form.get('branch', 'General')
    semester = request.form.get('semester', '1')
    resource_type = request.form.get('category', 'notes')
    subject = request.form.get('formSubject', 'General')
    description = request.form.get('description', '')
    year = request.form.get('year', datetime.now().year)
    
    print(f"Form data: branch={branch}, subject={subject}, type={resource_type}, files={len(files)}")
    
    # Try to get Google Drive service
    service = get_drive_service()
    use_google_drive = service is not None
    
    if use_google_drive:
        print("✅ Using Google Drive for upload")
    else:
        print("⚠️ Google Drive not available, using local storage fallback")
    
    for file in files:
        if file.filename == '':
            continue
        
        print(f"\n📁 Processing: {file.filename}")
        
        if not allowed_file(file.filename):
            errors.append(f'File type not allowed: {file.filename}')
            print(f"❌ File type not allowed")
            continue
        
        try:
            # Save file temporarily
            filename = secure_filename(file.filename)
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(temp_path)
            print(f"✅ Saved to temp: {temp_path}")
            
            # Get file size
            file_size = os.path.getsize(temp_path)
            if file_size > 1024*1024:
                file_size_str = f"{file_size/(1024*1024):.2f} MB"
            elif file_size > 1024:
                file_size_str = f"{file_size/1024:.2f} KB"
            else:
                file_size_str = f"{file_size} Bytes"
            
            # Create title
            title = f"{subject} - {filename}"
            
            # Upload to storage
            print("🔄 Uploading file...")
            
            if use_google_drive:
                # Try Google Drive first
                file_id, drive_url, error = upload_to_drive(service, temp_path, filename, branch)
                if error:
                    print(f"⚠️ Google Drive upload failed: {error}")
                    print("🔄 Falling back to local storage...")
                    # Fallback to local storage
                    file_id, drive_url, error = upload_to_local_fallback(temp_path, filename, branch)
            else:
                # Use local storage
                file_id, drive_url, error = upload_to_local_fallback(temp_path, filename, branch)
            
            if error:
                print(f"❌ Upload failed: {error}")
                errors.append(f'Failed to upload {filename}: {error}')
                os.remove(temp_path)
                continue
            
            print(f"✅ Got file_id: {file_id}")
            print(f"✅ Got drive_url: {drive_url}")
            
            # Create Resource record
            resource = Resource(
                title=title,
                description=description,
                file_id=file_id,
                drive_url=drive_url,
                file_name=filename,
                file_type=file.content_type or 'application/octet-stream',
                file_size=file_size_str,
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
            print(f"✅ Added to DB session. Total: {uploaded_count}")
            
            # Clean up temp file
            os.remove(temp_path)
            print("✅ Cleaned temp file")
            
        except Exception as e:
            error_msg = f'Error processing {file.filename}: {str(e)}'
            errors.append(error_msg)
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"\n📊 DEBUG SUMMARY:")
    print(f"   Files processed: {len(files)}")
    print(f"   Successfully uploaded: {uploaded_count}")
    print(f"   Errors: {len(errors)}")
    
    # Commit to database
    if uploaded_count > 0:
        try:
            db.session.commit()
            print(f"✅ Database commit successful for {uploaded_count} files")
            
            saved_count = Resource.query.filter_by(uploader_id=current_user.id).count()
            print(f"✅ User now has {saved_count} total resources in database")
            
            storage_type = "Google Drive" if use_google_drive else "local storage"
            flash(f'✅ Successfully uploaded {uploaded_count} file(s) to {storage_type}!', 'success')
        except Exception as e:
            db.session.rollback()
            print(f"❌ Database commit failed: {e}")
            flash(f'❌ Database error: {str(e)}', 'error')
            uploaded_count = 0
    else:
        flash('❌ No files were uploaded', 'error')
    
    # Show individual errors
    for error in errors[:5]:
        flash(f'⚠️ {error}', 'warning')
    
    print(f"ADMIN UPLOAD COMPLETED")
    
    return redirect(f'/{ADMIN_SECRET_PATH}')

@app.route('/admin/add-drive-resource', methods=['POST'])
@login_required
def add_drive_resource():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    
    # Get form data
    title = request.form.get('title')
    branch = request.form.get('branch')
    semester = request.form.get('semester')
    subject = request.form.get('subject')
    resource_type = request.form.get('resource_type')
    year = request.form.get('year')
    drive_url = request.form.get('drive_url')
    description = request.form.get('description')
    
    # Create resource
    resource = Resource(
        title=title,
        branch=branch,
        semester=semester,
        subject=subject,
        resource_type=resource_type,
        year=year,
        drive_url=drive_url,
        description=description,
        uploader_id=current_user.id,
        file_name=title,  # Use title as file name
        file_type='drive_link',  # Mark as drive link
        downloads=0,
        views=0
    )
    
    db.session.add(resource)
    db.session.commit()
    
    flash('Resource added successfully!', 'success')
    return redirect(url_for('admin_portal'))

# ================== INITIALIZATION ==================

with app.app_context():
    print("=" * 60)
    print("DATABASE INITIALIZATION")
    print("=" * 60)
    
    # Create all tables
    db.create_all()
    print("✅ Tables created")
    
    # Add drive_url column if missing
    try:
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('resource')]
        
        if 'drive_url' not in columns:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE resource ADD COLUMN drive_url VARCHAR(500)'))
            print("✅ Added 'drive_url' column to resource table")
    except Exception as e:
        print(f"Note: Could not check/update table structure: {e}")
    
    # Create admin user if doesn't exist
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
        print("✅ Admin user created")
    
    # Count existing data
    user_count = User.query.count()
    resource_count = Resource.query.count()
    print(f"✅ Database has {user_count} users and {resource_count} resources")
    print(f"✅ Local storage folder: {LOCAL_STORAGE}")
    print(f"✅ Upload folder: {UPLOAD_FOLDER}")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)