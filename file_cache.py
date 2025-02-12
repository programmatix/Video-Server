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
        return datetime.strptime(date_str, '%Y%m%d%H%M%S').astimezone()
    except (IndexError, ValueError):
        return None

class MediaFileIndex:
    def __init__(self):
        self.files = {}  # filename -> (datetime, type)
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

    def remove_file(self, filename):
        with self.lock:
            if filename in self.files:
                del self.files[filename]
                logger.info(f"Removed file from index: {filename}")

    def build_index(self, media_dir, audio_dir):
        start_time = datetime.now()
        logger.info("Starting to build media file index...")
        
        # Create temporary dict outside the lock
        temp_files = {}
        
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
                file_count += 1
                        
            logger.info(f"Completed scanning {directory} directory. Processed {file_count} files.")

        # Single lock to update the main files dict
        with self.lock:
            self.files.clear()
            self.files.update(temp_files)

        duration = datetime.now() - start_time
        logger.info(f"Finished building index in {duration.total_seconds():.2f}s. Total files indexed: {len(self.files)}")

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


class FileChangeHandler(FileSystemEventHandler):
    def __init__(self, index, directory):
        self.index = index
        self.directory = directory

    def on_created(self, event):
        if not event.is_directory:
            logger.info(f"File created: {event.src_path}")
            self.index.update_file(os.path.basename(event.src_path), self.directory)

    def on_deleted(self, event):
        if not event.is_directory:
            logger.info(f"File deleted: {event.src_path}")
            self.index.remove_file(os.path.basename(event.src_path))

    # def on_modified(self, event):
    #     if not event.is_directory:
    #         logger.info(f"File modified: {event.src_path}")
    #         self.index.update_file(os.path.basename(event.src_path), self.directory)
