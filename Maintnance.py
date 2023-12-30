from modelsntebles import Artist, Image, Tag, ImageTags, ArtistAlias, Website, db
import time
import requests
import json
import hashlib
import os
from tqdm import tqdm
import sys
import gzip
import shutil
import pandas as pd
from Tagger import _8305_Tagger


MAPPING = {
    "general": 0, "gen": 0,
    "artist": 1, "art": 1,
    "copyright": 3, "copy": 3, "co": 3,
    "character": 4, "char": 4, "ch": 4, "oc": 4,
    "species": 5, "spec": 5,
    "invalid": 6, "inv": 6,
    "meta": 7,
    "lore": 8, "lor": 8,
}

from datetime import datetime, timedelta
today = datetime.now()
yesterday = today - timedelta(days=1)

def download_and_extract_csv(url, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    # Download the file
    response = requests.get(url, stream=True)
    if response.status_code != 200:
        raise ValueError(f"Failed to download file: Status code {response.status_code}")

    # Save the gzipped file
    gzipped_file = output_path + ".gz"
    print(f"Saving gzipped file to {gzipped_file}")
    with open(gzipped_file, 'wb') as f:
        f.write(response.content)
    print("Extracting gzipped file...")
    # Extract the gzipped file
    with gzip.open(gzipped_file, 'rb') as f_in:
        with open(output_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    
    print(f"File downloaded and extracted to {output_path}")

def maintenance_tasks(ROOT_PATH, FA_FOLDER):
    MODELS_FOLDER = os.path.join(ROOT_PATH, 'models')
    print("Running maintenance task...")
    find_images_and_update_tags()
    print("Updating aesthetic scores...")
    GetAestheticScore(FA_FOLDER)
    print("Done!")

def GetAestheticScore(FA_FOLDER, app):
    with app.app_context():
        from Scorer.AE_Predictor import AestheticPredictor
        fold = sys.path[0]
        print(f"{fold}\models\e621-l14-rhoLoss.ckpt")
        pred = AestheticPredictor(f"{fold}\models\e621-l14-rhoLoss.ckpt", "openai/clip-vit-large-patch14", 'default', False)
        images_to_score = db.session.query(Image).filter(Image.scored == False).all()
        total_images = len(images_to_score)
        for index, image in enumerate(images_to_score):
            try:
                file_name = f"{image.file_name}{image.file_type}"
                image_path = os.path.join(FA_FOLDER, image.artist.name, file_name)
                if not os.path.isfile(image_path):
                    print(f"File not found: {image_path}")
                    continue
                score = pred.predict(image_path)
                if score is None:
                    print(f"Error processing {image_path}")
                    continue
                image.score = score
                image.scored = True
            except Exception as e:
                print(e)
            yield (index + 1), total_images
        db.session.commit()
        pred.close()
        del pred

def GetOriginalScore(FA_FOLDER, app):
    with app.app_context():
        import Scorer.Orig_Scorer
        fold = sys.path[0]
        pred = Scorer.Orig_Scorer.AestheticScorePredictor(f"{fold}\models\sac+logos+ava1-l14-linearMSE.pth")
        images_to_score = db.session.query(Image).filter(Image.AE_Scored == False).all()
        total_images = len(images_to_score)
        pred.load_model()
        for index, image in enumerate(images_to_score):
            try:
                file_name = f"{image.file_name}{image.file_type}"
                image_path = os.path.join(FA_FOLDER, image.artist.name, file_name)
                if not os.path.isfile(image_path):
                    print(f"File not found: {image_path}")
                    continue
                score = pred.predict(image_path)
                if score is None:
                    print(f"Error processing {image_path}")
                    continue
                image.AE_Score = score
                image.AE_Scored = True
            except Exception as e:
                print(e)
            yield (index + 1), total_images
        db.session.commit()
        pred.unload_model()
        del pred

def extract_md5_to_file(csv_path, md5_file_path):
    print(f"Extracting MD5 values from {csv_path}...")
    df = pd.read_csv(csv_path, usecols=['md5'])  # Load only the 'md5' column
    print(f"Saving MD5 values to {md5_file_path}...")
    df.to_csv(md5_file_path, index=False, header=False)
    print(f"MD5 values saved to {md5_file_path}")

def delete_file(file_path):
    if os.path.exists(file_path):
        os.remove(file_path)
        print(f"Deleted file: {file_path}")
    else:
        print(f"File not found: {file_path}")

def is_md5_in_file(md5, md5_file_path):
    with open(md5_file_path, 'r') as file:
        if md5 in file.read():
            return True
    return False

def prepFIAUT(csv_path, md5_file_path):
    if not os.path.exists(md5_file_path):
        download_and_extract_csv(f"https://e621.net/db_export/posts-{yesterday.year}-{yesterday.month}-{yesterday.day}.csv.gz", csv_path)
        extract_md5_to_file(csv_path, md5_file_path)
    if os.path.exists(csv_path):
        delete_file(csv_path)
    if os.path.exists(csv_path + ".gz"):
        delete_file(csv_path + ".gz")


def find_images_and_update_tags(app, use_csv=False):
    csv_path = os.path.join(app.root_path, 'CSV', f'posts-{yesterday.year}-{yesterday.month}-{yesterday.day}.csv.csv')
    md5_path = os.path.join(app.root_path, 'CSV', 'md5s.csv')
    prepFIAUT(csv_path, md5_path)
    with app.app_context():
        # Step 1: Find images with MD5 and missing tags
        images_without_tags = db.session.query(Image).\
            outerjoin(ImageTags, Image.id == ImageTags.image_id).\
            filter(Image.md5.isnot(None)).\
            filter(Image.check_again == True).\
            filter(ImageTags.tag_id.is_(None)).\
            all()


        # Step 4: Apply returned tags
        total_images = len(images_without_tags)
        for index, image in enumerate(images_without_tags):
            iteration = image.checked_count + 1
            if iteration >= 2:
                image.check_again = False
            image.checked_count = iteration
            if is_md5_in_file(image.md5, md5_path) or not use_csv:
                time.sleep(1)
                tags = get_tags_for_md5(image.md5, image)
                print(image.md5)
                if use_csv and (tags is None or len(tags) == 0):
                    print(f"MD5 {image.md5} was found but no tags, possibly deleted.")
                    image.check_again = False
                if len(tags) > 0:
                    print(f"Found {len(tags)} tags for {image.md5}")
                    image.check_again = False
                for tag_name, category_int in tags.items():
                    tag = Tag.query.filter_by(tag_name=tag_name).first()
                    if not tag:
                        # Create new tag if it doesn't exist
                        latest_id = Tag.query.order_by(Tag.id.desc()).first()
                        new_id = (latest_id.id + 1) if latest_id else 1
                        tag = Tag(id=new_id, tag_name=tag_name, count=1, category=category_int)
                        db.session.add(tag)
                        db.session.commit()
                    countz = tag.count + 1
                    image.tags.append(tag)
                    tag.count = countz
                db.session.commit()
            yield (index + 1), total_images
        

def get_tags_for_md5(md5_values, imagedb):
    URL = f"https://e621.net/posts.json?tags=md5:{md5_values}"
    response = requests.get(URL, headers={'User-Agent': 'FurArchiver/1.0'})
    if response.status_code != 200:
        raise ValueError(f"Request failed with status code {response.status_code}")
    data = json.loads(response.content)
    if len(data['posts']) > 1:
        raise ValueError("More than one post returned, something went wrong")
    elif len(data['posts']) == 0:
        return {}
    mapped_tags = {}
    imagedb.check_again = False
    tags = data['posts'][0]['tags']
    print(tags)
    if tags is None:
        return None
    for category, tag_list in tags.items():
        category_id = MAPPING.get(category, None)
        if category_id is not None:
            for tag in tag_list:
                mapped_tags[tag] = category_id
    return mapped_tags

def tag_images_without_tags(app, FA_FOLDER):
    fold = sys.path[0]
    tagger = _8305_Tagger()
    tagger.load(f"{fold}\\models\\model_balanced.pth", f"{fold}\\models\\tags_8034.json")

    with app.app_context():
        images_without_tags = db.session.query(Image).\
            outerjoin(ImageTags, Image.id == ImageTags.image_id).\
            filter(ImageTags.tag_id.is_(None)).\
            all()

        total_images = len(images_without_tags)
        for index, image in enumerate(images_without_tags):
            artist_Name = Artist.query.filter_by(id=image.artist_id).first().name
            image_path = os.path.join(FA_FOLDER, artist_Name, f"{image.file_name}{image.file_type}")
            if os.path.exists(image_path):
                try:
                    generated_tags = tagger.predict_image(image_path)
                    print(f"Generated tags for {image.file_name}: {generated_tags}")
                    for tag_name in generated_tags:
                        tag_name = tag_name.replace(" ", "_")
                        tag = Tag.query.filter_by(tag_name=tag_name).first()
                        if not tag:
                            tag = Tag(tag_name=tag_name)
                            db.session.add(tag)
                        image.tags.append(tag)
                        image.autotagged = True
                    db.session.commit()
                except Exception as e:
                    print(f"Error processing image {image.file_name}: {e}")
            yield (index + 1), total_images

    tagger.close()

def sql_database_cleanup(app, FA_FOLDER):
    with app.app_context():
        images = db.session.query(Image).all()
        total_images = len(images)
        #artist array: ["Name": "ID"]
        artist_dict = {}
        artists = db.session.query(Artist).all()
        for artist in artists:
            artist_dict[artist.id] = artist.name
        print(artist_dict)
        for index, image in enumerate(images):
            if image.artist_id is None:
                print(f"Image {image.file_name}{image.file_type} has no artist ID, deleting from database...")
                db.session.delete(image)
                yield (index + 1), total_images  
                continue
            artist = artist_dict.get(image.artist_id)
            image_path = os.path.join(FA_FOLDER, artist, f"{image.file_name}{image.file_type}")
            if not os.path.exists(image_path):
                print(f"Image {image.file_name}{image.file_type} not found, deleting from database...")
                db.session.delete(image)
                yield (index + 1), total_images  
                continue
            if image.md5 is None:
                print(f"Image {image.file_name}{image.file_type} has no MD5 but exists on disk, making md5...")
                with open(image_path, 'rb') as file:
                    image.md5 = hashlib.md5(file.read()).hexdigest()
                yield (index + 1), total_images  
                continue
        #if artists have no images, delete them
        artists = db.session.query(Artist).all()
        for artist in artists:
            images = db.session.query(Image).filter(Image.artist_id == artist.id).all()
            if len(images) == 0:
                print(f"Artist {artist.name} has no images, deleting from database...")
                db.session.delete(artist)
                continue
        db.session.commit()          
