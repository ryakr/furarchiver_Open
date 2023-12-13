import os
from os.path import splitext
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory
from Downloader import Downloader
from flask_sqlalchemy import SQLAlchemy
from modelsntebles import Artist, Image, Tag, ImageTags, ArtistAlias, Website, db
import requests
import json
import time
from tqdm import tqdm
import math
import Maintnance
app = Flask(__name__)
base_url = "http://g6jy5jkx466lrqojcngbnksugrcfxsl562bzuikrka5rv7srgguqbjid.onion/fa/"
socks_proxy_port = "9150"
config_file = os.path.join(app.root_path, "config.json")  # This should be the path to your config.json file
QueueActive = False

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///FurArchiver.db'
db.init_app(app)

# Create an instance of your Downloader
downloader = Downloader(base_url, socks_proxy_port, config_file, db.session, app)
FA_FOLDER = os.path.join(app.root_path, 'FA')

def query_database_with_sorting(query, sort_by):
    if sort_by == 'name':
        return query.order_by(Image.file_name)
    elif sort_by in ['score', 'score_desc']:
        # Filter out records where score is 0.0 or null
        query = query.filter(Image.score != 0.0, Image.score.isnot(None))
        if sort_by == 'score':
            return query.order_by(Image.score)
        else:  # sort_by == 'score_desc'
            return query.order_by(Image.score.desc())
    else:  # Default sorting
        return query.order_by(Image.id)

@app.route('/artists')
def artists_grid():
    page = request.args.get('page', 1, type=int)  # Default to first page
    per_page = 50  # Number of artists per page

    # Paginate the artists
    paginated_artists = Artist.query.paginate(page=page, per_page=per_page, error_out=False)

    artist_images = []
    for artist in paginated_artists.items:
        top_image = Image.query.filter_by(artist_id=artist.id).order_by(Image.score.desc()).first()
        if top_image:
            image_url = url_for('fa_file', artist_name=artist.name, filename=f"{top_image.file_name}{top_image.file_type}")
            artist_images.append((artist, image_url))

    return render_template('artists_grid.html', artist_images=artist_images, paginated_artists=paginated_artists)


@app.route('/R_MAINTENANCE', methods=['GET'])
def maintenance_route():
    Maintnance.maintenance_tasks(app.root_path, FA_FOLDER)
    return '', 204  # Returns an empty response with a 204 No Content status


@app.route('/')
def home():
    # Gather statistics for the index page
    artist_count, image_count, total_size = get_fa_stats()
    total_size_gb = round(total_size / (1024**3), 2)  # Convert bytes to gigabytes and round to 2 decimal places
    return render_template('index.html', artist_count=artist_count, image_count=image_count, total_size=total_size_gb)

@app.route('/search', methods=['GET', 'POST'])
def search_page():
    if request.method == 'POST':
        sort_by = request.form.get('sort_by', 'id') 
        artist_name = request.form.get('artist_name').lower()
        print("Artist name: ", artist_name)
        artist_path = os.path.join('FA', artist_name)

        if not os.path.isdir(artist_path):
            downloader.add_to_queue(artist_name)
            # You might want to trigger the downloader to start processing here
            # or have it run periodically in the background
            return render_template('results.html', error='Artist not found. Added to download queue.', sort_by=sort_by, artist_name=artist_name, download_queue=downloader.get_queue())
        else:
            # If artist is found, display their files
            files = os.listdir(artist_path)
            return render_template('results.html', artist_name=artist_name, sort_by=sort_by, files=files, download_queue=downloader.get_queue())

    # For a GET request, just display the search form
    return render_template('search.html')

@app.route('/results', methods=['GET', 'POST'])
def search_results():
    page = request.args.get('page', 1, type=int)  # Get the page number from query parameters
    artist_name = request.args.get('artist_name', '').lower()  # Adjust to get artist_name from GET request
    sort_by = request.args.get('sort_by', 'id')
    if request.method == 'POST':
        sort_by = request.form.get('sort_by', 'id') 
        artist_name = request.form.get('artist_name').lower()

    
    artist_path = os.path.join(FA_FOLDER, artist_name)
    
    if os.path.isdir(artist_path):
        # Implement pagination
        artist_id = Artist.query.filter_by(name=artist_name).first().id
        files_query = db.session.query(Image).filter(Image.artist_id == artist_id)  # Adjust query as needed
        files_query = query_database_with_sorting(files_query, sort_by)
        paginated_files = files_query.paginate(page=page, max_per_page=50, error_out=False)
        image_paths = []
        for image in paginated_files.items:
            image_paths.append(url_for('fa_file', artist_name=artist_name, filename=f"{image.file_name}{image.file_type}"))
        files = image_paths
        print(image_paths)
        total_pages = paginated_files.pages
        zipped_files = zip(files, paginated_files.items)
        return render_template('results.html', artist_name=artist_name, sort_by=sort_by, files=files, total_pages=total_pages, current_page=page, zipped_files=zipped_files)
    else:
        total_pages = 1
        return render_template('results.html', artist_name=artist_name, sort_by=sort_by, error='Artist not found', current_page=page, total_pages=total_pages)

@app.route('/fa/<artist_name>/<filename>')
def fa_file(artist_name, filename):
    artist_path = os.path.join(FA_FOLDER, artist_name)
    return send_from_directory(artist_path, filename, as_attachment=True)

@app.route('/download', methods=['POST'])
def download():
    artist_name = request.form.get('artist_name')
    downloader.add_to_queue(artist_name)
    downloader.process_queue(FA_FOLDER)
    # TODO: Here you might want to trigger the actual download process
    # This could be done in a background thread or a separate process
    return jsonify({'success': f'Download requested for {artist_name}'})

@app.route('/queue')
def queue():
    # Render a template that shows the download queue
    return render_template('queue.html', download_queue=downloader.get_queue())

@app.route('/queue-status')
def queue_status():
    queue_info = downloader.get_queue()  # This now directly returns the required list of dictionaries
    return jsonify(queue_info)



def get_fa_stats():
    # Placeholder function to gather statistics from the FA_FOLDER
    if not os.path.exists(FA_FOLDER):
        # If the FA_FOLDER doesn't exist, return zeros
        return 0, 0, 0

    artist_count = Artist.query.order_by(Artist.id.desc()).first().id
    image_count = Image.query.order_by(Image.id.desc()).first().id
    total_size = sum([os.path.getsize(os.path.join(r, file)) for r, d, files in os.walk(FA_FOLDER) for file in files])
    return artist_count, image_count, total_size

with app.app_context():
    # Perform operations that require the app context
    db.create_all()
    # Other setup or initialization code can go here

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)