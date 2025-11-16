from flask import Flask, jsonify, send_from_directory, abort, Response, render_template, request
from flask_cors import CORS
import os
from datetime import datetime, timedelta
import pandas as pd
import logging
from functools import lru_cache
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading
import re
from file_cache import MediaFileIndex
from file_cache import FileChangeHandler
import pytz
app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Directory where media files are stored
MEDIA_DIR = "/mnt/bigdisk3/motion"
AUDIO_MEDIA_DIR = "/mnt/bigdisk3/audio"


# Initialize index and build it
media_index = MediaFileIndex()
media_index.build_index(MEDIA_DIR, AUDIO_MEDIA_DIR)

# Set up file watchers (if the directories exist)
def schedule_directory_watch(observer, handler, directory_path, directory_name):
    if not os.path.isdir(directory_path):
        logger.warning(f"{directory_name.capitalize()} directory {directory_path} does not exist. File watcher disabled.")
        return False
    try:
        observer.schedule(handler, directory_path, recursive=False)
        logger.info(f"Watching {directory_name} directory for changes: {directory_path}")
        return True
    except FileNotFoundError:
        logger.warning(f"{directory_name.capitalize()} directory {directory_path} disappeared before watcher could start.")
        return False

observer = Observer()
media_handler = FileChangeHandler(media_index, "media")
audio_handler = FileChangeHandler(media_index, "audio")
media_watch_active = schedule_directory_watch(observer, media_handler, MEDIA_DIR, "media")
audio_watch_active = schedule_directory_watch(observer, audio_handler, AUDIO_MEDIA_DIR, "audio")

if media_watch_active or audio_watch_active:
    observer.start()
else:
    logger.warning("No media directories available; file change watchers not started.")

# Set up a background task to periodically scan for new JSON metadata
def metadata_scanner_task():
    while True:
        try:
            # Sleep first to give time for the server to start up
            time.sleep(300)  # Check every 5 minutes
            logger.info("Running periodic scan for new JSON metadata files")
            media_index.scan_for_missing_metadata()
        except Exception as e:
            logger.error(f"Error in metadata scanner task: {str(e)}")

# Start the metadata scanner in a separate thread
metadata_scanner_thread = threading.Thread(target=metadata_scanner_task, daemon=True)
metadata_scanner_thread.start()

def extract_timestamp_from_filename(filename):
    # First try the standard pattern (used by videos)
    timestamp_match = re.match(r'(\d{4})(\d{2})(\d{2})_?(\d{2})(\d{2})(\d{2})', filename)
    
    # If that fails, try the pattern with recording_ prefix
    if not timestamp_match:
        timestamp_match = re.match(r'recording_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', filename)
    
    if timestamp_match:
        year, month, day, hour, minute, second = map(int, timestamp_match.groups())
        try:
            dt = datetime(year, month, day, hour, minute, second)
            # Convert to UK timezone
            uk_tz = pytz.timezone('Europe/London')
            dt = uk_tz.localize(dt)
            return int(dt.timestamp() * 1000)  # Convert to milliseconds
        except ValueError:
            logger.error(f"Invalid date components in filename: {filename}")
    return None

def process_date_params(request):
    """Process date parameters, handling both start/end and day parameters"""
    start = request.args.get('start')
    end = request.args.get('end')
    day = request.args.get('day')
    
    # If day parameter is provided, it overrides start and end
    if day:
        try:
            # Create datetime at noon of specified day
            base_date = datetime.fromisoformat(day)
            # Ensure the datetime is naive (no timezone info)
            if base_date.tzinfo is not None:
                base_date = base_date.replace(tzinfo=None)
                
            start_date = datetime(base_date.year, base_date.month, base_date.day, 12, 0, 0)
            # Set end date to noon of next day
            end_date = start_date + timedelta(days=1)
            
            logger.info(f"Using day parameter: {day}, start={start_date.isoformat()}, end={end_date.isoformat()}")
            return start_date, end_date
        except ValueError as e:
            logger.error(f"Invalid day format: {day}, error: {str(e)}")
    
    # Process regular start/end parameters
    start_date = None
    end_date = None
    
    if start:
        start_date = datetime.fromisoformat(start)
        # Ensure the datetime is naive (no timezone info)
        if start_date.tzinfo is not None:
            start_date = start_date.replace(tzinfo=None)
            
    if end:
        end_date = datetime.fromisoformat(end)
        # Ensure the datetime is naive (no timezone info)
        if end_date.tzinfo is not None:
            end_date = end_date.replace(tzinfo=None)
    
    return start_date, end_date

# Update API endpoints to use index
@app.route('/api/files', methods=['GET'])
def list_files():
    try:
        start_time = datetime.now()
        
        # Process date parameters
        start_date, end_date = process_date_params(request)
        
        logger.info(f"Listing files with start={start_date}, end={end_date}")
        
        media_files = media_index.get_files(directory="media", start=start_date, end=end_date)
        media_files = [f for f in media_files if f.endswith(('.mp4', '.mkv', '.avi', '.mp3'))]

        duration = datetime.now() - start_time
        logger.info(f"Returning {len(media_files)} videos. Duration: {duration.total_seconds():.2f}s")
        return jsonify(media_files)
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos', methods=['GET'])
def list_videos():
    try:
        start_time = datetime.now()
        
        # Process date parameters
        start_date, end_date = process_date_params(request)
        
        logger.info(f"Listing videos with start={start_date}, end={end_date}")
        
        media_files = media_index.get_files(directory="media", start=start_date, end=end_date)
        video_files = [f for f in media_files if f.endswith(('.mp4', '.mkv', '.avi'))]
        
        result = []
        for filename in video_files:
            file_path = os.path.join(MEDIA_DIR, filename)
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            
            # Get metadata if available
            metadata = media_index.get_video_details(filename)
            
            # Create result object
            video_info = {
                "filename": filename,
                "file_size_in_bytes": file_size
            }
            
            # Extract timestamp from filename
            epoch_millis = extract_timestamp_from_filename(filename)
            if epoch_millis:
                video_info["filename_as_epoch_millis"] = epoch_millis
            
            # Add metadata if available
            if metadata:
                video_info.update(metadata)
                
            result.append(video_info)

        duration = datetime.now() - start_time
        logger.info(f"Returning {len(result)} video details. Duration: {duration.total_seconds():.2f}s")
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error listing video details: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/images', methods=['GET'])
def list_images():
    try:
        start_time = datetime.now()
        
        # Process date parameters
        start_date, end_date = process_date_params(request)
        
        logger.info(f"Listing images with start={start_date}, end={end_date}")
        
        media_files = media_index.get_files(directory="media", start=start_date, end=end_date)
        media_files = [f for f in media_files if f.endswith(('.jpg', '.png', '.jpeg'))]

        duration = datetime.now() - start_time
        logger.info(f"Returning {len(media_files)} images. Duration: {duration.total_seconds():.2f}s")
        return jsonify(media_files)
    except Exception as e:
        logger.error(f"Error listing images: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/audio', methods=['GET'])
def list_audio():
    try:
        start_time = datetime.now()
        
        # Process date parameters
        start_date, end_date = process_date_params(request)
        
        logger.info(f"Listing audio with start={start_date}, end={end_date}")
        
        media_files = media_index.get_files(directory="audio", start=start_date, end=end_date)
        media_files = [f for f in media_files if f.endswith(('.mp3', '.opus', '.ogg', '.wav'))]

        duration = datetime.now() - start_time
        logger.info(f"Returning {len(media_files)} audio files. Duration: {duration.total_seconds():.2f}s")
        return jsonify(media_files)
    except Exception as e:
        logger.error(f"Error listing audio: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/audio2', methods=['GET'])
def list_audio_detailed():
    try:
        start_time = datetime.now()
        
        # Process date parameters
        start_date, end_date = process_date_params(request)
        
        logger.info(f"Listing detailed audio with start={start_date}, end={end_date}")
        
        media_files = media_index.get_files(directory="audio", start=start_date, end=end_date)
        audio_files = [f for f in media_files if f.endswith(('.mp3', '.opus', '.ogg', '.wav'))]
        
        result = []
        for filename in audio_files:
            file_path = os.path.join(AUDIO_MEDIA_DIR, filename)
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            
            # Get metadata if available
            metadata = media_index.get_audio_details(filename)
            
            # Create result object
            audio_info = {
                "filename": filename,
                "file_size_in_bytes": file_size,
                "duration_ms": 30000  # Fixed duration of 30 seconds (30000 ms)
            }
            
            # Extract timestamp from filename
            epoch_millis = extract_timestamp_from_filename(filename)
            if epoch_millis:
                audio_info["filename_as_epoch_millis"] = epoch_millis
            
            # Add metadata if available
            if metadata:
                audio_info["metadata"] = metadata
                
            result.append(audio_info)

        duration = datetime.now() - start_time
        logger.info(f"Returning {len(result)} detailed audio files. Duration: {duration.total_seconds():.2f}s")
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error listing detailed audio: {str(e)}")
        return jsonify({'error': str(e)}), 500

# REST API to stream a media file (returns raw data)
@app.route('/media/<filename>', methods=['GET'])
def get_media(filename):
    try:
        logger.info(f"Retrieving media file: {filename}")
        return send_from_directory(MEDIA_DIR, filename)
    except FileNotFoundError:
        try:
            logger.info(f"Media file not found in MEDIA_DIR, trying AUDIO_MEDIA_DIR: {filename}")
            return send_from_directory(AUDIO_MEDIA_DIR, filename)
        except FileNotFoundError:
            logger.error(f"File not found: {filename}")
            return jsonify({'error': 'File not found'}), 404

@app.route('/audio/<filename>', methods=['GET'])
def get_audio(filename):
    try:
        logger.info(f"Retrieving audio file: {filename}")
        return send_from_directory(AUDIO_MEDIA_DIR, filename)
    except FileNotFoundError:
        logger.error(f"Audio file not found: {filename}")
        return jsonify({'error': 'File not found'}), 404

# Override 404 error to return JSON instead of HTML
@app.errorhandler(404)
def page_not_found(e):
    return jsonify({'error': 'Not found'}), 404

# Override 500 error to return JSON instead of HTML
@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error'}), 500

# New API endpoint to return data size
@app.route('/data_size', methods=['GET'])
def data_size():
    try:
        start_time = datetime.now()
        
        # Process date parameters
        start_date, end_date = process_date_params(request)
        
        logger.info(f"Generating data size report with start={start_date}, end={end_date}")
        
        data = []
        for directory in [MEDIA_DIR, AUDIO_MEDIA_DIR]:
            for root, _, files in os.walk(directory):
                for file in files:
                    file_path = os.path.join(root, file)
                    mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                    
                    if start_date and mtime < start_date.replace(tzinfo=None):
                        continue
                    if end_date and mtime > end_date.replace(tzinfo=None):
                        continue
                        
                    size = os.path.getsize(file_path) / (1024 * 1024)
                    date = mtime.date()
                    ext = os.path.splitext(file)[1].lower()
                    
                    if ext in ['.mp4', '.mkv', '.avi']:
                        media_type = 'video'
                    elif ext in ['.jpg', '.png', '.jpeg']:
                        media_type = 'image'
                    elif ext in ['.mp3', '.opus', '.ogg', '.wav']:
                        media_type = 'audio'
                    else:
                        continue
                    
                    data.append({'date': date, 'type': media_type, 'size': size})

        duration = datetime.now() - start_time
        logger.info(f"Generated data size report with {len(data)} entries. Duration: {duration.total_seconds():.2f}s")
        df = pd.DataFrame(data)
        df = df.groupby(['date', 'type'])['size'].sum().unstack(fill_value=0)
        df['total'] = df.sum(axis=1)
        df = df.reset_index().sort_values('date', ascending=False)
        
        total_video = df['video'].sum()
        total_image = df['image'].sum()
        total_audio = df['audio'].sum()
        total_all = df['total'].sum()
        
        html = '''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Data Size Report</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f0f0f0; }
                table { width: 100%; border-collapse: collapse; background-color: white; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
                th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
                th { background-color: #4CAF50; color: white; }
                tr:nth-child(even) { background-color: #f2f2f2; }
                tr:hover { background-color: #ddd; }
                .total-row { font-weight: bold; background-color: #e6f3ff; }
            </style>
        </head>
        <body>
            <h1>Data Size Report</h1>
            <table>
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Video (MiB)</th>
                        <th>Image (MiB)</th>
                        <th>Audio (MiB)</th>
                        <th>Total (MiB)</th>
                    </tr>
                </thead>
                <tbody>
        '''
        
        for _, row in df.iterrows():
            html += f'''
                    <tr>
                        <td>{row['date']}</td>
                        <td>{row.get('video', 0):.2f}</td>
                        <td>{row.get('image', 0):.2f}</td>
                        <td>{row.get('audio', 0):.2f}</td>
                        <td>{row['total']:.2f}</td>
                    </tr>
            '''
        
        html += f'''
                    <tr class="total-row">
                        <td>Total</td>
                        <td>{total_video:.2f}</td>
                        <td>{total_image:.2f}</td>
                        <td>{total_audio:.2f}</td>
                        <td>{total_all:.2f}</td>
                    </tr>
                </tbody>
            </table>
        </body>
        </html>
        '''
        
        return html
    except Exception as e:
        logger.error(f"Error generating data size report: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
