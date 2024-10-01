from flask import Flask, jsonify, send_from_directory, abort, Response, render_template
from flask_cors import CORS
import os
from datetime import datetime
import pandas as pd

app = Flask(__name__)
CORS(app)

# Directory where media files are stored
MEDIA_DIR = "/home/graham/motion"
AUDIO_MEDIA_DIR = "/home/graham/audio"

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

@app.route('/api/images', methods=['GET'])
def list_images():
    try:
        files = os.listdir(MEDIA_DIR)
        media_files = [f for f in files if f.endswith(('.jpg', '.png', '.jpeg'))]
        return jsonify(media_files)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/audio', methods=['GET'])
def list_audio():
    try:
        files = os.listdir(AUDIO_MEDIA_DIR)
        media_files = [f for f in files if f.endswith(('.mp3', '.opus', '.ogg', '.wav'))]
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

# New API endpoint to return data size
@app.route('/data_size', methods=['GET'])
def data_size():
    data = []
    for directory in [MEDIA_DIR, AUDIO_MEDIA_DIR]:
        for root, _, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                size = os.path.getsize(file_path) / (1024 * 1024)  # Convert to MiB
                date = datetime.fromtimestamp(os.path.getmtime(file_path)).date()
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
