import json
from datetime import datetime
import os
import logging
import threading
from watchdog.events import FileSystemEventHandler


logger = logging.getLogger(__name__)

def parse_media_date(filename, is_video=False, is_audio=False):
    try:
        if is_video:
            # movie_filename %Y%m%d_%H%M%S
            # 20250212_064159.mkv
            date_str = filename[:15].replace('_', '')
        elif is_audio:
            # recording_20241209_064622.opus
            date_str = filename[10:25].replace('_', '')
        else:
            # 57-20250212071300-snapshot.jpg  
            # picture_filename %Y%m%d_%H%M%S-%q
            date_str = filename.split('-')[1][:14]
        
        # Create a naive datetime (no timezone)
        dt = datetime.strptime(date_str, '%Y%m%d%H%M%S')
        return dt
    except (IndexError, ValueError):
        return None

class MediaFileIndex:
    def __init__(self):
        self.files = {}  # filename -> (datetime, type)
        self.video_metadata = {}  # filename -> metadata from JSON file
        self.lock = threading.Lock()

    def update_file(self, filename, directory):
        if not filename.endswith(('.mp4', '.mkv', '.avi', '.mp3', '.jpg', '.png', '.jpeg', '.opus', '.ogg', '.wav')):
            return
            
        is_video = filename.endswith(('.mp4', '.mkv', '.avi'))
        is_audio = filename.endswith(('.mp3', '.opus', '.ogg', '.wav'))
        date = parse_media_date(filename, is_video=is_video, is_audio=is_audio)
        
        if date:
            with self.lock:
                self.files[filename] = (date, directory)
                logger.info(f"Added/updated file in index: {filename} with date {date}")
                
                # Check for associated JSON metadata for videos
                if is_video and directory == "media":
                    self.update_video_metadata(filename)

    def update_video_metadata(self, video_filename):
        json_filename = os.path.splitext(video_filename)[0] + ".json"
        
        # Get the media directory path directly
        if hasattr(self, 'media_dir'):
            media_dir = self.media_dir
        else:
            media_dir = next((dir_path for name, dir_path in self.get_directories() if name == "media"), None)
        
        if not media_dir:
            logger.error("Media directory not found")
            return
            
        json_path = os.path.join(media_dir, json_filename)
        
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    metadata = json.load(f)
                self.video_metadata[video_filename] = metadata
                logger.info(f"Loaded metadata for video: {video_filename}")
            except Exception as e:
                logger.error(f"Error loading metadata for {video_filename}: {str(e)}")
                if video_filename in self.video_metadata:
                    del self.video_metadata[video_filename]
        else:
            # Remove metadata if JSON file no longer exists
            if video_filename in self.video_metadata:
                del self.video_metadata[video_filename]

    def remove_file(self, filename):
        with self.lock:
            if filename in self.files:
                del self.files[filename]
                logger.info(f"Removed file from index: {filename}")
            
            # Remove metadata if it's a video file
            if filename in self.video_metadata:
                del self.video_metadata[filename]
                logger.info(f"Removed metadata for: {filename}")

    def get_directories(self):
        return []  # Will be implemented in the actual code

    def build_index(self, media_dir, audio_dir):
        start_time = datetime.now()
        logger.info("Starting to build media file index...")
        
        # Store directories for later use
        self.media_dir = media_dir
        self.audio_dir = audio_dir
        
        # Create temporary dict outside the lock
        temp_files = {}
        temp_metadata = {}
        
        for directory, dir_path in [("media", media_dir), ("audio", audio_dir)]:
            file_count = 0
            logger.info(f"Scanning {directory} directory: {dir_path}")
            
            for entry in os.scandir(dir_path):
                if file_count % 10000 == 0:
                    logger.info(f"Processed {file_count} files in {directory} directory...")
                    
                if not entry.name.endswith(('.mp4', '.mkv', '.avi', '.mp3', '.jpg', '.png', '.jpeg', '.opus', '.ogg', '.wav')):
                    continue
                    
                is_video = entry.name.endswith(('.mp4', '.mkv', '.avi'))
                is_audio = entry.name.endswith(('.mp3', '.opus', '.ogg', '.wav'))
                date = parse_media_date(entry.name, is_video=is_video, is_audio=is_audio)
                
                if date:
                    temp_files[entry.name] = (date, directory)
                    
                    # Check for associated JSON metadata for videos
                    if is_video and directory == "media":
                        json_filename = os.path.splitext(entry.name)[0] + ".json"
                        json_path = os.path.join(dir_path, json_filename)
                        
                        if os.path.exists(json_path):
                            try:
                                with open(json_path, 'r') as f:
                                    metadata = json.load(f)
                                temp_metadata[entry.name] = metadata
                            except Exception as e:
                                logger.error(f"Error loading metadata for {entry.name}: {str(e)}")
                                
                file_count += 1
                        
            logger.info(f"Completed scanning {directory} directory. Processed {file_count} files.")

        # Single lock to update the main files dict
        with self.lock:
            self.files.clear()
            self.files.update(temp_files)
            
            self.video_metadata.clear()
            self.video_metadata.update(temp_metadata)

        duration = datetime.now() - start_time
        logger.info(f"Finished building index in {duration.total_seconds():.2f}s. Total files indexed: {len(self.files)}")
        logger.info(f"Loaded metadata for {len(self.video_metadata)} video files")

    def get_files(self, directory=None, start=None, end=None):
        with self.lock:
            files = []
            for filename, (date, file_dir) in self.files.items():
                if directory and file_dir != directory:
                    continue
                    
                if start and date < start:
                    continue
                if end and date > end:
                    continue
                    
                files.append(filename)
            return files
            
    def get_video_details(self, video_filename):
        with self.lock:
            metadata = self.video_metadata.get(video_filename, {})
            return metadata

    def get_directories(self):
        return [
            ("media", self.media_dir),
            ("audio", self.audio_dir)
        ]

    def scan_for_missing_metadata(self):
        """Scan for JSON metadata files that may have been added after video files were indexed"""
        logger.info("Scanning for newly-added JSON metadata files...")
        
        if not hasattr(self, 'media_dir') or not self.media_dir:
            logger.error("Media directory not set, cannot scan for missing metadata")
            return
        
        count = 0
        with self.lock:
            for filename, (date, directory) in self.files.items():
                # Only check video files in the media directory
                if directory == "media" and filename.endswith(('.mp4', '.mkv', '.avi')):
                    # If we don't already have metadata for this video
                    if filename not in self.video_metadata:
                        # Check if a JSON file exists
                        json_filename = os.path.splitext(filename)[0] + ".json"
                        json_path = os.path.join(self.media_dir, json_filename)
                        
                        if os.path.exists(json_path):
                            try:
                                with open(json_path, 'r') as f:
                                    metadata = json.load(f)
                                self.video_metadata[filename] = metadata
                                count += 1
                                if count % 100 == 0:
                                    logger.info(f"Loaded {count} missing metadata files so far...")
                            except Exception as e:
                                logger.error(f"Error loading metadata for {filename}: {str(e)}")
        
        logger.info(f"Finished scanning for missing metadata. Found and loaded {count} files.")


class FileChangeHandler(FileSystemEventHandler):
    def __init__(self, index, directory):
        self.index = index
        self.directory = directory

    def on_created(self, event):
        if not event.is_directory:
            filename = os.path.basename(event.src_path)
            logger.info(f"File created: {event.src_path}")
            
            # Handle normal files (videos, images, audio)
            if filename.endswith(('.mp4', '.mkv', '.avi', '.mp3', '.jpg', '.png', '.jpeg', '.opus', '.ogg', '.wav')):
                self.index.update_file(filename, self.directory)
            
            # Handle JSON metadata files
            elif filename.endswith('.json') and self.directory == "media":
                # Extract video filename by removing .json extension
                video_filename = os.path.splitext(filename)[0]
                # Add file extensions to check for
                for ext in ['.mp4', '.mkv', '.avi']:
                    potential_video = video_filename + ext
                    # Check if we have this video in our index
                    with self.index.lock:
                        if potential_video in self.index.files:
                            logger.info(f"Updating metadata for video {potential_video} from new JSON file")
                            self.index.update_video_metadata(potential_video)
                            break

    def on_deleted(self, event):
        if not event.is_directory:
            filename = os.path.basename(event.src_path)
            logger.info(f"File deleted: {event.src_path}")
            
            # Handle normal files (videos, images, audio)
            if filename.endswith(('.mp4', '.mkv', '.avi', '.mp3', '.jpg', '.png', '.jpeg', '.opus', '.ogg', '.wav')):
                self.index.remove_file(filename)
            
            # Handle JSON metadata files
            elif filename.endswith('.json') and self.directory == "media":
                # Extract video filename by removing .json extension
                video_filename = os.path.splitext(filename)[0]
                # Add file extensions to check for
                for ext in ['.mp4', '.mkv', '.avi']:
                    potential_video = video_filename + ext
                    # Check if we have this video in our index
                    with self.index.lock:
                        if potential_video in self.index.files and potential_video in self.index.video_metadata:
                            logger.info(f"Removing metadata for video {potential_video} as JSON file was deleted")
                            del self.index.video_metadata[potential_video]
                            break

    def on_modified(self, event):
        if not event.is_directory:
            filename = os.path.basename(event.src_path)
            
            # Only handle JSON metadata files for modification events
            if filename.endswith('.json') and self.directory == "media":
                logger.info(f"JSON metadata file modified: {event.src_path}")
                # Extract video filename by removing .json extension
                video_filename = os.path.splitext(filename)[0]
                # Add file extensions to check for
                for ext in ['.mp4', '.mkv', '.avi']:
                    potential_video = video_filename + ext
                    # Check if we have this video in our index
                    with self.index.lock:
                        if potential_video in self.index.files:
                            logger.info(f"Updating metadata for video {potential_video} from modified JSON file")
                            self.index.update_video_metadata(potential_video)
                            break
