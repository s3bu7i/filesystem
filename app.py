from flask import Flask, request, jsonify, send_file, render_template_string, redirect, url_for
import sqlite3
import os
import uuid
import datetime
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import mimetypes

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = 'uploads'
DATABASE = 'fileshare.db'
MAX_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1GB in bytes
ALLOWED_EXTENSIONS = None  # Allow all file types

app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def init_database():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Create files table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            mime_type TEXT,
            description TEXT,
            uploader TEXT,
            upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            download_count INTEGER DEFAULT 0,
            last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create download_logs table for tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS download_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT,
            download_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_address TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id)
        )
    ''')
    
    conn.commit()
    conn.close()

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def allowed_file(filename):
    """Check if file extension is allowed - now allows all files"""
    return True  # Allow all file types

def cleanup_old_files():
    """Remove files older than 30 days"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get files older than 30 days
    thirty_days_ago = datetime.datetime.now() - datetime.timedelta(days=30)
    cursor.execute('''
        SELECT stored_name FROM files 
        WHERE last_accessed < ? OR upload_date < ?
    ''', (thirty_days_ago, thirty_days_ago))
    
    old_files = cursor.fetchall()
    
    # Delete physical files
    for file_row in old_files:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_row['stored_name'])
        if os.path.exists(file_path):
            os.remove(file_path)
    
    # Delete database records
    cursor.execute('''
        DELETE FROM files 
        WHERE last_accessed < ? OR upload_date < ?
    ''', (thirty_days_ago, thirty_days_ago))
    
    conn.commit()
    conn.close()

@app.route('/')
def index():
    """Serve the main HTML page"""
    with open('index.html', 'r', encoding='utf-8') as f:
        html_content = f.read()
    return render_template_string(html_content)

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file uploads"""
    try:
        print("=== UPLOAD DEBUG ===")
        print(f"Files in request: {list(request.files.keys())}")
        
        if 'file' not in request.files:
            print("ERROR: No 'file' in request.files")
            return redirect(url_for('index'))
        
        file = request.files['file']
        print(f"File object: {file}")
        print(f"Filename: {file.filename}")
        
        if file.filename == '':
            print("ERROR: Empty filename")
            return redirect(url_for('index'))
        
        if file and allowed_file(file.filename):
            print("File validation passed")
            
            # Generate unique filename
            file_id = str(uuid.uuid4())
            original_name = secure_filename(file.filename)
            stored_name = file_id + '_' + original_name
            
            print(f"Generated filename: {stored_name}")
            
            # Save file
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], stored_name)
            print(f"Saving to: {file_path}")
            
            file.save(file_path)
            print("File saved successfully")
            
            # Get file info
            file_size = os.path.getsize(file_path)
            mime_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
            
            print(f"File size: {file_size} bytes")
            print(f"MIME type: {mime_type}")
            
            # Save to database
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO files (id, original_name, stored_name, file_size, mime_type, description, uploader)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (file_id, original_name, stored_name, file_size, mime_type, 
                  request.form.get('description', ''), request.form.get('uploader', 'Anonymous')))
            conn.commit()
            conn.close()
            
            print("Database record created successfully")
            print("=== UPLOAD COMPLETE ===")
            
            return redirect(url_for('index'))
        else:
            print("ERROR: File validation failed")
            return redirect(url_for('index'))
    
    except RequestEntityTooLarge:
        print("ERROR: File too large")
        return "File too large! Maximum file size is 1GB.", 413
    except Exception as e:
        print(f"ERROR: Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Upload error: {str(e)}", 500

@app.route('/download/<file_id>')
def download_file(file_id):
    """Handle file downloads"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get file info
    cursor.execute('SELECT * FROM files WHERE id = ?', (file_id,))
    file_info = cursor.fetchone()
    
    if not file_info:
        conn.close()
        return "File not found!", 404
    
    # Update download count and last accessed
    cursor.execute('''
        UPDATE files 
        SET download_count = download_count + 1, last_accessed = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (file_id,))
    
    # Log download
    cursor.execute('''
        INSERT INTO download_logs (file_id, ip_address)
        VALUES (?, ?)
    ''', (file_id, request.remote_addr))
    
    conn.commit()
    conn.close()
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_info['stored_name'])
    
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name=file_info['original_name'])
    else:
        return "File not found on disk!", 404

@app.route('/delete/<file_id>', methods=['POST', 'DELETE'])
def delete_file(file_id):
    """Handle file deletion"""
    try:
        print(f"=== DELETE REQUEST for file_id: {file_id} ===")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get file info before deletion
        cursor.execute('SELECT * FROM files WHERE id = ?', (file_id,))
        file_info = cursor.fetchone()
        
        if not file_info:
            print("File not found in database")
            conn.close()
            return jsonify({'success': False, 'message': 'File not found'}), 404
        
        print(f"Found file: {file_info['original_name']}")
        
        # Delete physical file
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_info['stored_name'])
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Physical file deleted: {file_path}")
        else:
            print(f"Physical file not found: {file_path}")
        
        # Delete from database
        cursor.execute('DELETE FROM download_logs WHERE file_id = ?', (file_id,))
        cursor.execute('DELETE FROM files WHERE id = ?', (file_id,))
        
        conn.commit()
        conn.close()
        
        print(f"File deleted successfully: {file_info['original_name']}")
        return jsonify({'success': True, 'message': 'File deleted successfully'})
        
    except Exception as e:
        print(f"ERROR deleting file: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Delete error: {str(e)}'}), 500

@app.route('/api/files')
def api_files():
    """API endpoint to get all files"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, original_name, description, uploader, upload_date, file_size, download_count
            FROM files 
            ORDER BY upload_date DESC
        ''')
        files = cursor.fetchall()
        conn.close()
        
        return jsonify([dict(row) for row in files])
    except Exception as e:
        print(f"ERROR in api_files: {str(e)}")
        return jsonify([]), 500

@app.route('/api/stats')
def api_stats():
    """API endpoint to get site statistics"""
    try:
        print("=== STATS REQUEST ===")
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Total files
        cursor.execute('SELECT COUNT(*) as count FROM files')
        total_files = cursor.fetchone()['count']
        print(f"Total files: {total_files}")
        
        # Total storage used
        cursor.execute('SELECT SUM(file_size) as size FROM files')
        total_size_result = cursor.fetchone()
        total_size = total_size_result['size'] or 0
        print(f"Total size: {total_size}")
        
        # Files uploaded today
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        cursor.execute('SELECT COUNT(*) as count FROM files WHERE DATE(upload_date) = ?', (today,))
        files_today = cursor.fetchone()['count']
        print(f"Files today: {files_today}")
        
        conn.close()
        
        result = {
            'total_files': total_files,
            'total_size': total_size,
            'files_today': files_today
        }
        print(f"Stats result: {result}")
        
        return jsonify(result)
        
    except Exception as e:
        print(f"ERROR in api_stats: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'total_files': 0,
            'total_size': 0,
            'files_today': 0
        }), 500

@app.route('/admin/cleanup')
def admin_cleanup():
    """Admin endpoint to manually trigger cleanup"""
    cleanup_old_files()
    return "Cleanup completed!"

@app.errorhandler(413)
def too_large(e):
    return "File is too large! Maximum file size is 1GB.", 413

@app.errorhandler(404)
def not_found(e):
    return "Page not found!", 404

@app.errorhandler(500)
def server_error(e):
    return "Internal server error!", 500

if __name__ == '__main__':
    # Initialize database on startup
    init_database()
    
    # Run cleanup on startup
    cleanup_old_files()
    
    print("🚀 SimpleShare File Sharing Server Starting...")
    print("📁 Upload folder:", UPLOAD_FOLDER)
    print("💾 Database:", DATABASE)
    print("📊 Max file size: 1GB")
    print("🌐 Server will run on http://localhost:5000")
    
    app.run(debug=True, host='0.0.0.0', port=5000)