import json
from datetime import datetime
import os
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
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
            if filename.startswith('recording_'):
                date_str = filename[10:25].replace('_', '')
            else:
                # Handle other audio filename formats if needed
                date_str = filename[:15].replace('_', '')
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
        self.audio_metadata = {}  # filename -> metadata from JSON file
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
                
                # Check for associated JSON metadata for videos and audio
                if (is_video and directory == "media") or (is_audio and directory == "audio"):
                    self.update_metadata(filename, directory)

    def update_metadata(self, filename, directory):
        json_filename = os.path.splitext(filename)[0] + ".json"
        
        # Get the appropriate directory path
        if directory == "media":
            dir_path = self.media_dir
            metadata_dict = self.video_metadata
        else:
            dir_path = self.audio_dir
            metadata_dict = self.audio_metadata
        
        if not dir_path:
            logger.error(f"{directory} directory not found")
            return
            
        json_path = os.path.join(dir_path, json_filename)
        
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    metadata = json.load(f)
                metadata_dict[filename] = metadata
                logger.info(f"Loaded metadata for {directory} file: {filename}")
            except Exception as e:
                logger.error(f"Error loading metadata for {filename}: {str(e)}")
                if filename in metadata_dict:
                    del metadata_dict[filename]
        else:
            # Remove metadata if JSON file no longer exists
            if filename in metadata_dict:
                del metadata_dict[filename]

    def remove_file(self, filename):
        with self.lock:
            if filename in self.files:
                del self.files[filename]
                logger.info(f"Removed file from index: {filename}")
            
            # Remove metadata if it's a video or audio file
            if filename in self.video_metadata:
                del self.video_metadata[filename]
                logger.info(f"Removed video metadata for: {filename}")
            if filename in self.audio_metadata:
                del self.audio_metadata[filename]
                logger.info(f"Removed audio metadata for: {filename}")

    def get_directories(self):
        return []  # Will be implemented in the actual code

    def build_index(self, media_dir, audio_dir):
        start_time = datetime.now()
        logger.info("Starting to build media file index...")
        
        # Store directories for later use (when available)
        self.media_dir = media_dir if os.path.isdir(media_dir) else None
        if not self.media_dir:
            logger.warning(f"Media directory not found: {media_dir}. Index will start empty.")
        self.audio_dir = audio_dir if os.path.isdir(audio_dir) else None
        if not self.audio_dir:
            logger.warning(f"Audio directory not found: {audio_dir}. Index will start empty.")
        
        # Create temporary dict outside the lock
        temp_files = {}
        temp_video_metadata = {}
        temp_audio_metadata = {}
        
        directories_to_scan = []
        if self.media_dir:
            directories_to_scan.append(("media", self.media_dir))
        if self.audio_dir:
            directories_to_scan.append(("audio", self.audio_dir))

        worker_count = max(1, min(32, os.cpu_count() or 4))

        def scan_directory(directory, dir_path):
            directory_files = {}
            directory_video_metadata = {}
            directory_audio_metadata = {}
            entries = []
            
            try:
                with os.scandir(dir_path) as it:
                    for entry in it:
                        entries.append((entry.name, entry.path))
            except Exception as e:
                logger.error(f"Error scanning {dir_path}: {str(e)}")
                return directory_files, directory_video_metadata, directory_audio_metadata
            
            logger.info(f"Scanning {directory} directory with {len(entries)} entries using {worker_count} workers...")
            valid_extensions = ('.mp4', '.mkv', '.avi', '.mp3', '.jpg', '.png', '.jpeg', '.opus', '.ogg', '.wav')
            
            def process_entry(item):
                filename, file_path = item
                if not filename.endswith(valid_extensions):
                    return None
                
                is_video = filename.endswith(('.mp4', '.mkv', '.avi'))
                is_audio = filename.endswith(('.mp3', '.opus', '.ogg', '.wav'))
                date = parse_media_date(filename, is_video=is_video, is_audio=is_audio)
                if not date:
                    return None
                
                metadata_type = None
                metadata = None
                if (is_video and directory == "media") or (is_audio and directory == "audio"):
                    json_filename = os.path.splitext(filename)[0] + ".json"
                    json_path = os.path.join(dir_path, json_filename)
                    if os.path.exists(json_path):
                        try:
                            with open(json_path, 'r') as f:
                                metadata = json.load(f)
                            metadata_type = "video" if is_video else "audio"
                        except Exception as e:
                            logger.error(f"Error loading metadata for {filename}: {str(e)}")
                            metadata = None
                return filename, (date, directory), metadata_type, metadata
            
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                for idx, result in enumerate(executor.map(process_entry, entries, chunksize=256), start=1):
                    if idx % 10000 == 0:
                        logger.info(f"Processed {idx} files in {directory} directory...")
                    if not result:
                        continue
                    filename, file_record, metadata_type, metadata = result
                    directory_files[filename] = file_record
                    if metadata_type == "video" and metadata is not None:
                        directory_video_metadata[filename] = metadata
                    elif metadata_type == "audio" and metadata is not None:
                        directory_audio_metadata[filename] = metadata
            
            logger.info(f"Completed scanning {directory} directory. Processed {len(entries)} files.")
            return directory_files, directory_video_metadata, directory_audio_metadata

        for directory, dir_path in directories_to_scan:
            dir_files, dir_video_metadata, dir_audio_metadata = scan_directory(directory, dir_path)
            temp_files.update(dir_files)
            temp_video_metadata.update(dir_video_metadata)
            temp_audio_metadata.update(dir_audio_metadata)

        # Single lock to update the main files dict
        with self.lock:
            self.files.clear()
            self.files.update(temp_files)
            
            self.video_metadata.clear()
            self.video_metadata.update(temp_video_metadata)
            
            self.audio_metadata.clear()
            self.audio_metadata.update(temp_audio_metadata)

        duration = datetime.now() - start_time
        logger.info(f"Finished building index in {duration.total_seconds():.2f}s. Total files indexed: {len(self.files)}")
        logger.info(f"Loaded metadata for {len(self.video_metadata)} video files and {len(self.audio_metadata)} audio files")

    def get_files(self, directory=None, start=None, end=None):
        with self.lock:
            files = []
            for filename, (date, file_dir) in self.files.items():
                if directory and file_dir != directory:
                    continue
                    
                # Make date comparison timezone consistent
                file_date = date
                if file_date.tzinfo is not None:
                    file_date = file_date.replace(tzinfo=None)
                    
                if start and file_date < start:
                    continue
                if end and file_date > end:
                    continue
                    
                files.append(filename)
            return files
            
    def get_video_details(self, video_filename):
        with self.lock:
            metadata = self.video_metadata.get(video_filename, {})
            return metadata

    def get_audio_details(self, audio_filename):
        with self.lock:
            metadata = self.audio_metadata.get(audio_filename, {})
            return metadata

    def get_directories(self):
        return [
            ("media", self.media_dir),
            ("audio", self.audio_dir)
        ]

    def scan_for_missing_metadata(self):
        """Scan for JSON metadata files that may have been added after video/audio files were indexed"""
        logger.info("Scanning for newly-added JSON metadata files...")
        
        has_media_dir = bool(getattr(self, 'media_dir', None))
        has_audio_dir = bool(getattr(self, 'audio_dir', None))
        if not has_media_dir and not has_audio_dir:
            logger.error("Media and audio directories not set, cannot scan for missing metadata")
            return
        
        count = 0
        with self.lock:
            for filename, (date, directory) in self.files.items():
                # Check video files in the media directory
                if directory == "media" and filename.endswith(('.mp4', '.mkv', '.avi')):
                    if not self.media_dir:
                        continue
                    if filename not in self.video_metadata:
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
                
                # Check audio files in the audio directory
                elif directory == "audio" and filename.endswith(('.mp3', '.opus', '.ogg', '.wav')):
                    if not self.audio_dir:
                        continue
                    if filename not in self.audio_metadata:
                        json_filename = os.path.splitext(filename)[0] + ".json"
                        json_path = os.path.join(self.audio_dir, json_filename)
                        
                        if os.path.exists(json_path):
                            try:
                                with open(json_path, 'r') as f:
                                    metadata = json.load(f)
                                self.audio_metadata[filename] = metadata
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
                            self.index.update_metadata(potential_video, "media")
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
                            self.index.update_metadata(potential_video, "media")
                            break
