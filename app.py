import os
from os.path import splitext
from flask import Flask, render_template, request, jsonify, Response, url_for, send_from_directory, flash, redirect
from Downloader import Downloader
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_, func, distinct
from modelsntebles import Artist, Image, Tag, ImageTags, ArtistAlias, Website, db
import json
import time
import Maintnance
from threading import Thread
from datetime import datetime
import signal
import sys
import zipfile
import re
import bleach
from werkzeug.utils import secure_filename
import hashlib
from tqdm import tqdm
import html

app = Flask(__name__)
base_url = "http://g6jy5jkx466lrqojcngbnksugrcfxsl562bzuikrka5rv7srgguqbjid.onion/fa/"
socks_proxy_port = "9150"
config_file = os.path.join(app.root_path, "config.json")  # This should be the path to your config.json file
QueueActive = False

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///FurArchiver.db'
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_default_secret_key')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024 # 10 GB max upload size
print(app.secret_key)

db.init_app(app)
FA_FOLDER = os.path.join(app.root_path, 'FA')
# Create an instance of your Downloader
downloader = Downloader(base_url, socks_proxy_port, config_file, db.session, FA_FOLDER, app)

def sanitize_input(user_input):
    # Sanitize the input by stripping all HTML tags and attributes
    clean_input = bleach.clean(user_input, tags=[], attributes={}, strip=True)
    return clean_input

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
    elif sort_by in ['original_score', 'original_score_desc']:
        # Filter out records where score is 0.0 or null
        query = query.filter(Image.AE_Score != 0.0, Image.AE_Score.isnot(None))
        if sort_by == 'original_score':
            return query.order_by(Image.AE_Score)
        else:  # sort_by == 'original_score_desc'
            return query.order_by(Image.AE_Score.desc())
    else:  # Default sorting
        return query.order_by(Image.id)

task_info = {
    'aesthetic_scoring': {'current': 0, 'total': 0, 'start_time': None, 'running': False},
    'tag_update': {'current': 0, 'total': 0, 'start_time': None, 'running': False},
    'tagger': {'current': 0, 'total': 0, 'start_time': None, 'running': False},
    'original_aesthetic_scoring': {'current': 0, 'total': 0, 'start_time': None, 'running': False},
    'sql_database_cleanup': {'current': 0, 'total': 0, 'start_time': None, 'running': False},
}

def calculate_estimated_time(task_key):
    info = task_info.get(task_key)
    
    if info is not None and isinstance(info, dict):
        current = info.get('current', 0)
        start_time = info.get('start_time')
        total = info.get('total', 0)

        if current > 0 and start_time:
            elapsed_time = (datetime.now() - start_time).total_seconds()
            total_time = (elapsed_time / current) * total
            remaining_time = total_time - elapsed_time
            return remaining_time  # Returns time in seconds
    
    return None

@app.route('/results/download/<search_input>', methods=['POST'])
def download_artist_images(search_input):
    use_score_threshold = request.form.get('use_score_threshold') == 'on'
    score_threshold = float(request.form.get('score_threshold', 0.0))
    score_direction = request.form.get('score_direction')
    include_tags = request.form.get('include_tags') == 'on'
    print(search_input)
    images_query = search_filter(search_input)

    if use_score_threshold:
        if score_direction == 'above':
            images_query = images_query.filter(Image.score >= score_threshold)
        elif score_direction == 'below':
            images_query = images_query.filter(Image.score <= score_threshold)

    images = images_query.all()

    zip_file_path = create_zip_file_for_artist(images, search_input, include_tags)
    if zip_file_path:
        directory = os.path.dirname(zip_file_path)
        filename = os.path.basename(zip_file_path)
        return send_from_directory(directory=directory, path=filename, as_attachment=True)
    else:
        return "Error in creating zip file", 500


def create_zip_file_for_artist(images, search_input, include_tags):
    search_input = sanitize_input(search_input.replace(':', '_'))
    cache_dir = os.path.join(app.root_path, 'cache', search_input)
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
    zip_filename = f"{search_input}_{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
    zip_filepath = os.path.join(cache_dir, zip_filename)

    with zipfile.ZipFile(zip_filepath, 'w') as zipf:
        for image in images:
            artist_id = image.artist_id
            artist_name = Artist.query.filter_by(id=artist_id).first().name
            image_path = os.path.join(FA_FOLDER, artist_name, image.file_name + image.file_type)
            if os.path.exists(image_path):
                # Write the image file to the zip at the root level
                zipf.write(image_path, os.path.basename(image_path))
                if include_tags:
                    tag_file_path = create_or_get_tag_file(image, cache_dir, artist_name)
                    # Write the tag file to the zip at the root level
                    zipf.write(tag_file_path, os.path.basename(tag_file_path))

    return zip_filepath if os.path.exists(zip_filepath) else None


def create_or_get_tag_file(image, cache_dir, artist_name):
    # Rejoin all parts except the last one to get the name without the extension
    tag_file_name = image.file_name + '.txt'
    print(tag_file_name)
    tag_file_path = os.path.join(cache_dir, tag_file_name)
    
    # Create tag file if it doesn't exist
    if not os.path.exists(tag_file_path):
        with open(tag_file_path, 'w', encoding='utf-8') as tag_file:
            if artist_name not in image.tags:
                tag_file.write(artist_name + ', ')
            for tag in image.tags:
                tag_to_write = tag.tag_name.replace('_', ' ')
                #if not last, append comma
                if tag != image.tags[-1]:
                    tag_file.write(tag_to_write + ', ')
                else:
                    tag_file.write(tag_to_write)
    
    return tag_file_path


# Add a function to check allowed file extensions
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        artist_name = request.form['artist_name']

        # Check if the artist exists, else create a new artist
        artist = Artist.query.filter_by(name=artist_name).first()
        if not artist:
            artist = Artist(name=artist_name)
            db.session.add(artist)
            db.session.commit()

        # Check if the post request has the file part
        if 'file[]' not in request.files:
            flash('No file part')
            return redirect(request.url)

        files = request.files.getlist('file[]')

        for file in tqdm(files):
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = os.path.splitext(filename)[1]
                file_path = os.path.join(FA_FOLDER, artist_name, filename)
                fileName_without_ext = os.path.splitext(filename)[0]

                # Save the file to the server
                if not os.path.exists(os.path.dirname(file_path)):
                    os.makedirs(os.path.dirname(file_path))
                file.save(file_path)

                # Calculate MD5 hash
                md5_hash = hashlib.md5()
                with open(file_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        md5_hash.update(chunk)
                md5 = md5_hash.hexdigest()

                # Create a new Image record and associate it with the artist
                new_image = Image(file_name=fileName_without_ext, file_type=file_ext, artist=artist, md5=md5)
                db.session.add(new_image)

        db.session.commit()  # Commit all new records to the database
        flash(f'{len(files)} files successfully uploaded')
        return redirect(url_for('upload_file'))

    return render_template('upload.html')



def create_zip_file(score_threshold, include_tags, include_all_images):
    images = Image.query

    if not include_all_images:
        if score_threshold:
            images = images.filter(Image.score >= score_threshold)
        if include_tags:
            images = images.filter(Image.tags.any(Tag.name.in_(include_tags)))

    zip_filename = f"images_{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
    zip_filepath = os.path.join(FA_FOLDER, zip_filename)

    with zipfile.ZipFile(zip_filepath, 'w') as zipf:
        for image in images:
            image_path = os.path.join(FA_FOLDER, image.file_name + image.file_type)
            if os.path.exists(image_path):
                zipf.write(image_path, os.path.basename(image_path))

    return zip_filepath if os.path.exists(zip_filepath) else None

def run_sql_database_cleanup():
    print("Starting SQL database cleanup...")
    task_info['sql_database_cleanup']['start_time'] = datetime.now()  # Set the start time
    for current, total in Maintnance.sql_database_cleanup(app, FA_FOLDER):
        task_info['sql_database_cleanup']['current'] = current
        task_info['sql_database_cleanup']['total'] = total
    task_info['sql_database_cleanup']['current'] = total
    task_info['sql_database_cleanup']['running'] = False

def run_original_aesthetic_scoring():
    print("Starting aesthetic scoring...")
    task_info['original_aesthetic_scoring']['start_time'] = datetime.now()  # Set the start time
    for current, total in Maintnance.GetOriginalScore(FA_FOLDER, app):
        task_info['original_aesthetic_scoring']['current'] = current
        task_info['original_aesthetic_scoring']['total'] = total
    task_info['original_aesthetic_scoring']['current'] = total
    task_info['original_aesthetic_scoring']['running'] = False

def run_aesthetic_scoring():
    print("Starting aesthetic scoring...")
    task_info['aesthetic_scoring']['start_time'] = datetime.now()  # Set the start time
    for current, total in Maintnance.GetAestheticScore(FA_FOLDER, app):
        task_info['aesthetic_scoring']['current'] = current
        task_info['aesthetic_scoring']['total'] = total
    task_info['aesthetic_scoring']['current'] = total
    task_info['aesthetic_scoring']['running'] = False

def run_tagger():
    print("Starting aesthetic scoring...")
    task_info['tagger']['start_time'] = datetime.now()  # Set the start time
    for current, total in Maintnance.tag_images_without_tags(app, FA_FOLDER):
        task_info['tagger']['current'] = current
        task_info['tagger']['total'] = total
    task_info['tagger']['current'] = total
    task_info['tagger']['running'] = False

def run_tag_update():
    task_info['tag_update']['start_time'] = datetime.now() 
    for current, total in Maintnance.find_images_and_update_tags(app, use_csv=True):
        task_info['tag_update']['current'] = current
        task_info['tag_update']['total'] = total
    task_info['tag_update']['current'] = total
    task_info['tag_update']['running'] = False

@app.route('/start_sql_database_cleanup')
def start_sql_database_cleanup():
    print(task_info['sql_database_cleanup'])
    if task_info['sql_database_cleanup']['running'] == False: 
        task_info['sql_database_cleanup']['running'] = True
        Thread(target=run_sql_database_cleanup).start()
    return jsonify({'status': 'started'})

@app.route('/sql_database_cleanup_progress')
def sql_database_cleanup_progress():
    print(task_info['sql_database_cleanup'])
    progress = task_info['sql_database_cleanup']
    current = progress.get('current', 0)
    total = progress.get('total', 0)
    est_time = calculate_estimated_time('sql_database_cleanup')
    return jsonify({'current': current, 'total': total, 'est_time': est_time})

@app.route('/start_original_scoring')
def start_original_aesthetic_scoring():
    print(task_info['original_aesthetic_scoring'])
    if task_info['original_aesthetic_scoring']['running'] == False: 
        task_info['original_aesthetic_scoring']['running'] = True
        Thread(target=run_original_aesthetic_scoring).start()
    return jsonify({'status': 'started'})

@app.route('/original_scoring_progress')
def original_aesthetic_scoring_progress():
    print(task_info['original_aesthetic_scoring'])
    progress = task_info['original_aesthetic_scoring']
    current = progress.get('current', 0)
    total = progress.get('total', 0)
    est_time = calculate_estimated_time('original_aesthetic_scoring')
    return jsonify({'current': current, 'total': total, 'est_time': est_time})

@app.route('/update_all_artists')
def update_all_artists():
    artists = Artist.query.all()
    for artist in artists:
        if artist.from_e621:
            downloader.add_to_queue(artist.name, "e621")
        if artist.from_furaffinity:
            downloader.add_to_queue(artist.name, "default")
    return jsonify({'status': 'All artists added to download queue'})

@app.route('/start_aesthetic_scoring')
def start_aesthetic_scoring():
    print(task_info['aesthetic_scoring'])
    if task_info['aesthetic_scoring']['running'] == False: 
        task_info['aesthetic_scoring']['running'] = True
        Thread(target=run_aesthetic_scoring).start()
    return jsonify({'status': 'started'})

@app.route('/aesthetic_scoring_progress')
def aesthetic_scoring_progress():
    print(task_info['aesthetic_scoring'])
    progress = task_info['aesthetic_scoring']
    current = progress.get('current', 0)
    total = progress.get('total', 0)
    est_time = calculate_estimated_time('aesthetic_scoring')
    return jsonify({'current': current, 'total': total, 'est_time': est_time})

@app.route('/start_tagger')
def start_tagger():
    print(task_info['tagger'])
    if task_info['tagger']['running'] == False: 
        task_info['tagger']['running'] = True
        Thread(target=run_tagger).start()
    return jsonify({'status': 'started'})

@app.route('/tagger_progress')
def tagger_progress():
    print(task_info['tagger'])
    progress = task_info['tagger']
    current = progress.get('current', 0)
    total = progress.get('total', 0)
    est_time = calculate_estimated_time('tagger')
    return jsonify({'current': current, 'total': total, 'est_time': est_time})

@app.route('/start_tag_update')
def start_tag_update():
    if task_info['tag_update']['running'] == False:  # Only start if not already running
        task_info['tag_update']['running'] = True
        Thread(target=run_tag_update).start()
    return jsonify({'status': 'started'})

@app.route('/tag_update_progress')
def tag_update_progress():
    progress = task_info['tag_update']
    est_time = calculate_estimated_time('tag_update')
    return jsonify({'current': progress['current'], 'total': progress['total'], 'est_time': est_time})



@app.route('/artists')
def artists_grid():
    page = request.args.get('page', 1, type=int)  # Default to first page
    per_page = 28  # Number of artists per page

    # Paginate the artists
    paginated_artists = Artist.query.paginate(page=page, per_page=per_page, error_out=False)

    artist_images = []
    for artist in paginated_artists.items:
        #get top image but only if it's not deleted
        top_image = Image.query.filter_by(artist_id=artist.id, Deleted=False).order_by(Image.score.desc()).first()
        #top_image = Image.query.filter_by(artist_id=artist.id).order_by(Image.score.desc()).first()
        if top_image:
            image_url = url_for('fa_file', artist_name=artist.name, filename=f"{top_image.file_name}{top_image.file_type}")
            artist_images.append((artist, image_url))

    return render_template('artists_grid.html', artist_images=artist_images, paginated_artists=paginated_artists)


@app.route('/R_MAINTENANCE', methods=['GET'])
def maintenance_route():
    Maintnance.maintenance_tasks(app.root_path, FA_FOLDER)
    return '', 204  # Returns an empty response with a 204 No Content status

@app.route('/maintenance')
def maintenance():
    return render_template('maintenance.html')

@app.route('/browse')
def browse_images():
    page = request.args.get('page', 1, type=int)  # Get the page number from query parameters

    # Use the search_filter function to get all images that are not deleted
    files_query = search_filter("", sort_by="id")
    paginated_files = files_query.paginate(page=page, max_per_page=50, error_out=False)

    image_paths = [url_for('fa_file', artist_name=image.artist.name, filename=f"{image.file_name}{image.file_type}") for image in paginated_files.items]
    edited_status = [image.Edited for image in paginated_files.items]
    total_pages = paginated_files.pages
    total_results = paginated_files.total
    zipped_files = zip(image_paths, paginated_files.items, edited_status)

    return render_template('results.html', total_results=total_results, search_input="Browse All Images", sort_by="id", files=image_paths, total_pages=total_pages, current_page=page, zipped_files=zipped_files)

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

def search_filter(search_input, sort_by="id", artist_name=None):
    files_query = db.session.query(Image)
    dangerous = html.unescape(search_input)
    search_input = sanitize_input(search_input)
    # Use regular expressions to find tags and artist
    tag_match = re.search(r"tags:([a-zA-Z0-9, _]+?)(?=\s(artist:|score:|tags:)|$)", dangerous)
    artist_match = re.search(r"artist:([a-zA-Z0-9 -_]+?)(?=\s(artist:|score:|tags:)|$)", dangerous)
    score_match = re.search(r"score:([><][0-9.]+?)(?=\s(artist:|score:|tags:)|$)", dangerous)

    print(search_input.lower() == "")
    print(search_input.lower() == "browse all images")
    print(artist_name == None)
    print(tag_match, artist_match, score_match)
    #if browse all images, skip regex
    if (not search_input == "" and not search_input.lower() == "browse all images") or not artist_name == None:            
        if tag_match:
            tags = tag_match.group(1).split(',')
            formatted_tags = [tag.strip().replace(' ', '_') for tag in tags if tag.strip()]
            tag_count_subquery = db.session.query(ImageTags.image_id, func.count(ImageTags.tag_id).label('tag_count')) \
                                        .join(Tag, ImageTags.tag_id == Tag.id) \
                                        .filter(Tag.tag_name.in_(formatted_tags)) \
                                        .group_by(ImageTags.image_id) \
                                        .having(func.count(ImageTags.tag_id) == len(formatted_tags)) \
                                        .subquery()
            files_query = files_query.join(tag_count_subquery, Image.id == tag_count_subquery.c.image_id)

        if artist_match:
            print(artist_match)
            artist_name_RE = artist_match.group(1).strip()
            artist_id = Artist.query.filter_by(name=artist_name_RE).first()
            if artist_id:
                artist_id = artist_id.id
                files_query = files_query.filter(Image.artist_id == artist_id)
            else:
                return files_query.filter(Image.id == -1)

        if score_match:
            print(score_match)
            operator, score = score_match.group(1)[0], float(score_match.group(1)[1:])
            comparison = Image.score > score if operator == '>' else Image.score < score
            files_query = files_query.filter(comparison)
        if artist_name or not (tag_match or artist_match or score_match):
            if not (tag_match or artist_match or score_match) and not artist_name:
                artist_name = search_input
            search_input += f" artist:{artist_name}"
            artist_id = Artist.query.filter_by(name=artist_name).first()
            if artist_id:
                artist_id = artist_id.id
                files_query = files_query.filter(Image.artist_id == artist_id)
            else:
                return files_query.filter(Image.id == -1)
    #not deleted
    files_query = files_query.filter(Image.Deleted == False)
    files_query = query_database_with_sorting(files_query, sort_by)
    return files_query

@app.route('/get-tags/<int:image_id>')
def get_tags(image_id):
    # Fetch tags from database
    tags = Image.query.get(image_id).tags
    return jsonify({'tags': [tag.tag_name for tag in tags]})

@app.route('/update-tags/<int:image_id>', methods=['POST'])
def update_tags(image_id):
    data = request.get_json()
    tags_to_add = data.get('tagsToAdd', [])
    tags_to_remove = data.get('tagsToRemove', [])
    
    # Update tags in the database for the image
    image = Image.query.get(image_id)
    if not image:
        return jsonify({'status': 'Image not found'}), 404
    print("Tags to add: ", tags_to_add)
    print("Tags to remove: ", tags_to_remove)
    #return jsonify({'status': 'success'})
    # Assuming you have a relationship set up for tags in your Image model
    # Add new tags
    for tag_name in tags_to_add:
        tag = Tag.query.filter_by(tag_name=tag_name).first()
        if not tag:
            # Create the tag if it doesn't exist
            tag = Tag(tag_name=tag_name)
            db.session.add(tag)
            # Assuming you commit at the end or have autocommit
        image.tags.append(tag)

    # Remove tags
    for tag_name in tags_to_remove:
        tag = Tag.query.filter_by(tag_name=tag_name).first()
        if tag:
            image.tags.remove(tag)
    image.Edited = True
    db.session.commit()

    return jsonify({'status': 'success'})

#Clear image.Edited
@app.route('/clear-edited/<int:image_id>', methods=['POST'])
def clear_edited(image_id):
    image = Image.query.get(image_id)
    if not image:
        return jsonify({'status': 'Image not found'}), 404
    image.Edited = False
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/delete-image/<int:image_id>', methods=['POST'])
def delete_image(image_id):
    image = Image.query.get(image_id)
    if not image:
        return jsonify({'status': 'Image not found'}), 404
    image_path = os.path.join(FA_FOLDER, image.artist.name, image.file_name + image.file_type)
    if os.path.exists(image_path):
        os.remove(image_path)
    image.Deleted = True
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/results', methods=['GET', 'POST'])
def search_results():
    page = request.args.get('page', 1, type=int)  # Get the page number from query parameters
    search_input = ""
    sort_by = "id"
    artist_name = ""
    if request.method == 'POST':
        search_input = request.form.get('search_input').lower().strip()
        artist_name = request.form.get('artist_name')
        if artist_name:
            artist_name = artist_name.lower().strip()
        sort_by = request.form.get('sort_by', 'id')
    elif request.method == 'GET':
        search_input = request.args.get('search_input', '').lower().strip()
        artist_name = request.args.get('artist_name')
        if artist_name:
            artist_name = artist_name.lower().strip()
        sort_by = request.args.get('sort_by', 'id')
    files_query = search_filter(search_input, sort_by, artist_name)

    search_input += f" artist:{artist_name}" if artist_name else ""

    paginated_files = files_query.paginate(page=page, max_per_page=50, error_out=False)
    image_paths = [url_for('fa_file', artist_name=image.artist.name, filename=f"{image.file_name}{image.file_type}") for image in paginated_files.items]
    edited_status = [image.Edited for image in paginated_files.items]
    total_pages = paginated_files.pages
    total_results = paginated_files.total
    zipped_files = zip(image_paths, paginated_files.items, edited_status)

    if len(image_paths) == 0:
        return render_template('results.html', error='No results found', sort_by=sort_by, search_input=search_input, files=image_paths, total_pages=total_pages, current_page=page, total_results=total_results, zipped_files=zipped_files)
    return render_template('results.html', total_results=total_results, search_input=search_input, sort_by=sort_by, files=image_paths, total_pages=total_pages, current_page=page, zipped_files=zipped_files)

@app.route('/fa/<artist_name>/<filename>')
def fa_file(artist_name, filename):
    artist_path = os.path.join(FA_FOLDER, artist_name)
    return send_from_directory(artist_path, filename, as_attachment=True)

@app.route('/download', methods=['POST'])
def download():
    search_input = request.form.get('search_input')
    print(search_input)
    artist_match = re.search(r"artist:([a-zA-Z0-9 _]+)", search_input)
    if artist_match:
        artist_name = artist_match.group(1).strip()
        source = request.form.get('sourceOption')
        print(source)
        downloader.add_to_queue(artist_name, source)
        downloader.process_queue()
        # TODO: Here you might want to trigger the actual download process
        # This could be done in a background thread or a separate process
        return jsonify({'success': f'Download requested for {artist_name}'})

@app.route('/queue')
def queue():
    # Render a template that shows the download queue
    return render_template('queue.html', download_queue=downloader.get_queue())

@app.route('/queue-status')
def queue_status():
    def generate():
        while True:
            queue_info = downloader.get_queue()
            yield f"data: {json.dumps(queue_info)}\n\n"
            time.sleep(2)  # Interval between updates

    return Response(generate(), mimetype='text/event-stream')



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
    cache_dir = os.path.join(app.root_path, 'cache')
    #delete cache folder if it exists
    if os.path.exists(cache_dir):
        import shutil
        shutil.rmtree(cache_dir)
    def signal_handler(sig, frame):
        print("Ctrl + C detected. Stopping...")
        downloader.stop_threads()  # Stop the threads gracefully
        print("Exiting...")
        os.kill(os.getpid(), signal.SIGINT)  # Send a SIGINT signal to the current process

    signal.signal(signal.SIGINT, signal_handler)
    app.run(debug=True, use_reloader=False)