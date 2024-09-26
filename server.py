from flask import Flask, jsonify, send_from_directory, abort, Response
import os

app = Flask(__name__)

# Directory where media files are stored
MEDIA_DIR = "/media/graham/C9F7-B545/motion/"

# REST API to list all media files (returns JSON)
@app.route('/api/files', methods=['GET'])
def list_files():
    try:
        files = os.listdir(MEDIA_DIR)
        # Only return specific media types like mp4, mkv, mp3, etc.
        media_files = [f for f in files if f.endswith(('.mp4', '.mkv', '.avi', '.mp3'))]
        return jsonify(media_files)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# REST API to stream a media file (returns raw data)
@app.route('/media/<filename>', methods=['GET'])
def get_media(filename):
    try:
        # Return the media file from the directory
        return send_from_directory(MEDIA_DIR, filename)
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404

# Override 404 error to return JSON instead of HTML
@app.errorhandler(404)
def page_not_found(e):
    return jsonify({'error': 'Not found'}), 404

# Override 500 error to return JSON instead of HTML
@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
