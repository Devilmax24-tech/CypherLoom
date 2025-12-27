-- Enhanced PostgreSQL Schema for CypherLoom

-- Users table with additional fields
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) UNIQUE NOT NULL,
    email VARCHAR(120) UNIQUE NOT NULL,
    password_hash VARCHAR(200) NOT NULL,
    full_name VARCHAR(100),
    profile_pic VARCHAR(200) DEFAULT 'default.jpg',
    bio TEXT,
    year VARCHAR(20),
    branch VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,
    is_admin BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    verification_token VARCHAR(200),
    reset_token VARCHAR(200),
    reset_token_expiry TIMESTAMP
);

-- Resources table with enhanced fields
CREATE TABLE IF NOT EXISTS resources (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    file_id VARCHAR(500),
    file_name VARCHAR(300),
    file_type VARCHAR(50),
    file_size VARCHAR(20),
    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resource_type VARCHAR(50) NOT NULL CHECK (resource_type IN ('notes', 'pyq', 'sample_paper', 'books')),
    year INTEGER,
    semester VARCHAR(10),
    branch VARCHAR(50),
    subject VARCHAR(100) NOT NULL,
    uploader_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    downloads INTEGER DEFAULT 0,
    views INTEGER DEFAULT 0,
    tags VARCHAR(500),
    is_approved BOOLEAN DEFAULT TRUE,
    rating DECIMAL(3,2) DEFAULT 0.0,
    rating_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP,
    metadata JSONB
);

-- Progress tracking with time tracking
CREATE TABLE IF NOT EXISTS progress (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER REFERENCES resources(id) ON DELETE SET NULL,
    subject VARCHAR(100) NOT NULL,
    topic VARCHAR(200),
    completed BOOLEAN DEFAULT FALSE,
    score DECIMAL(5,2),
    date_completed TIMESTAMP,
    notes TEXT,
    time_spent INTEGER DEFAULT 0, -- in minutes
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Uploads tracking
CREATE TABLE IF NOT EXISTS uploads (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Bookmarks/Favorites
CREATE TABLE IF NOT EXISTS bookmarks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, resource_id)
);

-- Ratings
CREATE TABLE IF NOT EXISTS ratings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    review TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, resource_id)
);

-- Downloads history
CREATE TABLE IF NOT EXISTS download_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ip_address INET,
    user_agent TEXT
);

-- Indexes for better performance
CREATE INDEX idx_resources_type ON resources(resource_type);
CREATE INDEX idx_resources_branch ON resources(branch);
CREATE INDEX idx_resources_semester ON resources(semester);
CREATE INDEX idx_resources_subject ON resources(subject);
CREATE INDEX idx_resources_year ON resources(year);
CREATE INDEX idx_resources_uploader ON resources(uploader_id);
CREATE INDEX idx_resources_upload_date ON resources(upload_date DESC);
CREATE INDEX idx_resources_rating ON resources(rating DESC);
CREATE INDEX idx_resources_downloads ON resources(downloads DESC);

CREATE INDEX idx_progress_user ON progress(user_id);
CREATE INDEX idx_progress_completed ON progress(completed);
CREATE INDEX idx_progress_subject ON progress(subject);
CREATE INDEX idx_progress_resource ON progress(resource_id);

CREATE INDEX idx_uploads_user ON uploads(user_id);
CREATE INDEX idx_uploads_resource ON uploads(resource_id);
CREATE INDEX idx_uploads_date ON uploads(upload_date DESC);

CREATE INDEX idx_bookmarks_user ON bookmarks(user_id);
CREATE INDEX idx_bookmarks_resource ON bookmarks(resource_id);

CREATE INDEX idx_ratings_resource ON ratings(resource_id);
CREATE INDEX idx_ratings_user_resource ON ratings(user_id, resource_id);

CREATE INDEX idx_downloads_user ON download_history(user_id);
CREATE INDEX idx_downloads_resource ON download_history(resource_id);
CREATE INDEX idx_downloads_date ON download_history(downloaded_at DESC);

-- Function to update resource rating
CREATE OR REPLACE FUNCTION update_resource_rating()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE resources 
    SET rating = (
        SELECT AVG(rating) 
        FROM ratings 
        WHERE resource_id = NEW.resource_id
    ),
    rating_count = (
        SELECT COUNT(*) 
        FROM ratings 
        WHERE resource_id = NEW.resource_id
    )
    WHERE id = NEW.resource_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for rating updates
CREATE TRIGGER trigger_update_rating
AFTER INSERT OR UPDATE OR DELETE ON ratings
FOR EACH ROW
EXECUTE FUNCTION update_resource_rating();

-- View for resource statistics
CREATE VIEW resource_stats AS
SELECT 
    r.id,
    r.title,
    r.resource_type,
    r.subject,
    r.branch,
    r.semester,
    r.year,
    r.downloads,
    r.views,
    r.rating,
    r.rating_count,
    u.username as uploader,
    COUNT(DISTINCT b.user_id) as bookmarks_count,
    COUNT(DISTINCT rh.user_id) as recent_downloads
FROM resources r
LEFT JOIN users u ON r.uploader_id = u.id
LEFT JOIN bookmarks b ON r.id = b.resource_id
LEFT JOIN download_history rh ON r.id = rh.resource_id 
    AND rh.downloaded_at > CURRENT_TIMESTAMP - INTERVAL '7 days'
WHERE r.is_approved = TRUE
GROUP BY r.id, u.username;

-- Materialized view for search optimization (refresh periodically)
CREATE MATERIALIZED VIEW search_index AS
SELECT 
    r.id,
    r.title,
    r.description,
    r.resource_type,
    r.subject,
    r.branch,
    r.tags,
    to_tsvector('english', 
        coalesce(r.title, '') || ' ' ||
        coalesce(r.description, '') || ' ' ||
        coalesce(r.subject, '') || ' ' ||
        coalesce(r.tags, '')
    ) as document
FROM resources r
WHERE r.is_approved = TRUE;

CREATE INDEX idx_search_index ON search_index USING gin(document);