import os
import tempfile
import logging
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
from pytube import YouTube
from pytube.exceptions import VideoUnavailable, RegexMatchError
from werkzeug.exceptions import BadRequest
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# CORS configuration - allows requests from your app
CORS(app, origins=[
    "http://localhost:3000",  # React development server
    "http://localhost:8080",  # Vue development server
    "https://yourdomain.com", # Your production domain
    # Add your app's domain here
])

# Security configuration
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size

# Rate limiting
limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Allowed file extensions and size limits
ALLOWED_EXTENSIONS = {'mp4', 'webm', 'mp3', 'wav'}
MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100MB

def validate_youtube_url(url):
    """Validate if the URL is a valid YouTube URL"""
    if not url or not isinstance(url, str):
        return False
    
    youtube_regex = re.compile(
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    )
    return bool(youtube_regex.match(url))

def sanitize_filename(filename):
    """Sanitize filename to prevent directory traversal"""
    # Remove or replace dangerous characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Limit filename length
    if len(filename) > 100:
        name, ext = os.path.splitext(filename)
        filename = name[:90] + ext
    return filename

@app.errorhandler(400)
def bad_request(error):
    return jsonify({'error': 'Bad request', 'message': str(error)}), 400

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'service': 'youtube-api-flask'})

@app.route('/info', methods=['POST'])
@limiter.limit("30 per minute")
def get_video_info():
    """Get video information and available formats"""
    try:
        # Validate request data
        if not request.is_json:
            raise BadRequest("Request must be JSON")
        
        data = request.get_json()
        if not data or 'url' not in data:
            raise BadRequest("Missing 'url' parameter")
        
        url = data['url'].strip()
        
        # Validate YouTube URL
        if not validate_youtube_url(url):
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        # Create YouTube object with error handling
        try:
            yt = YouTube(url)
            # Force loading of video info to catch errors early
            _ = yt.title
        except VideoUnavailable:
            return jsonify({'error': 'Video is unavailable or private'}), 404
        except RegexMatchError:
            return jsonify({'error': 'Invalid YouTube URL format'}), 400
        except Exception as e:
            logger.error(f"Error accessing video: {e}")
            return jsonify({'error': 'Failed to access video'}), 500
        
        # Get available formats
        formats = []
        try:
            for stream in yt.streams.filter(progressive=True):
                # Only include allowed file types
                file_type = stream.mime_type.split('/')[-1] if stream.mime_type else 'unknown'
                if file_type in ALLOWED_EXTENSIONS:
                    formats.append({
                        'quality': stream.resolution or 'audio only',
                        'type': file_type,
                        'itag': stream.itag,
                        'filesize': stream.filesize
                    })
        except Exception as e:
            logger.error(f"Error getting video formats: {e}")
            return jsonify({'error': 'Failed to retrieve video formats'}), 500
        
        if not formats:
            return jsonify({'error': 'No downloadable formats available'}), 404
        
        response_data = {
            'title': yt.title,
            'length': yt.length,
            'author': yt.author,
            'formats': formats
        }
        
        return jsonify(response_data)
        
    except BadRequest as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Unexpected error in get_video_info: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/download', methods=['POST'])
@limiter.limit("10 per minute")
def download_video():
    """Download video with specified format"""
    temp_file = None
    try:
        # Validate request data
        if not request.is_json:
            raise BadRequest("Request must be JSON")
        
        data = request.get_json()
        if not data or 'url' not in data or 'itag' not in data:
            raise BadRequest("Missing 'url' or 'itag' parameter")
        
        url = data['url'].strip()
        itag = data['itag']
        
        # Validate inputs
        if not validate_youtube_url(url):
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        if not isinstance(itag, int) or itag <= 0:
            return jsonify({'error': 'Invalid itag parameter'}), 400
        
        # Create YouTube object
        try:
            yt = YouTube(url)
            stream = yt.streams.get_by_itag(itag)
        except VideoUnavailable:
            return jsonify({'error': 'Video is unavailable or private'}), 404
        except Exception as e:
            logger.error(f"Error accessing video: {e}")
            return jsonify({'error': 'Failed to access video'}), 500
        
        if not stream:
            return jsonify({'error': 'Requested format not available'}), 404
        
        # Check file size limit
        if stream.filesize and stream.filesize > MAX_DOWNLOAD_SIZE:
            return jsonify({'error': 'File too large for download'}), 413
        
        # Check file type
        file_type = stream.mime_type.split('/')[-1] if stream.mime_type else 'unknown'
        if file_type not in ALLOWED_EXTENSIONS:
            return jsonify({'error': 'File type not allowed'}), 400
        
        # Create temporary directory for download
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Download to temporary location
            file_path = stream.download(output_path=temp_dir)
            
            # Sanitize filename
            original_filename = os.path.basename(file_path)
            safe_filename = sanitize_filename(original_filename)
            
            logger.info(f"Downloaded video: {yt.title} ({itag})")
            
            return send_file(
                file_path, 
                as_attachment=True, 
                download_name=safe_filename,
                mimetype=stream.mime_type
            )
            
        except Exception as e:
            logger.error(f"Error downloading video: {e}")
            return jsonify({'error': 'Failed to download video'}), 500
        
    except BadRequest as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Unexpected error in download_video: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# Cleanup function for temporary files
@app.teardown_appcontext
def cleanup_temp_files(error):
    """Clean up temporary files after request"""
    # This would be called after each request
    pass

if __name__ == '__main__':
    # Production-ready configuration
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '127.0.0.1')  # Changed from 0.0.0.0 for security
    
    if debug_mode:
        logger.warning("Running in debug mode - not recommended for production!")
    
    app.run(
        host=host,
        port=port,
        debug=debug_mode,
        threaded=True
    )
