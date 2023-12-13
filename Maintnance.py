from modelsntebles import Artist, Image, Tag, ImageTags, ArtistAlias, Website, db
import time
import requests
import json
from Scorer.AE_Predictor import AestheticPredictor
import os
from tqdm import tqdm
import sys

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

def maintenance_tasks(ROOT_PATH, FA_FOLDER):
    MODELS_FOLDER = os.path.join(ROOT_PATH, 'models')
    print("Running maintenance task...")
    find_images_and_update_tags()
    print("Updating aesthetic scores...")
    GetAestheticScore(FA_FOLDER)
    print("Done!")

def GetAestheticScore(FA_FOLDER, app):
    with app.app_context():
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

def find_images_and_update_tags(app):
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
            time.sleep(1)
            tags = get_tags_for_md5(image.md5, image)
            print(image.md5)
            iteration = image.checked_count + 1
            if iteration >= 5:
                image.check_again = False
            image.checked_count = iteration
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
            yield (index + 1), total_images
        
        db.session.commit()

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
    for category, tag_list in tags.items():
        category_id = MAPPING.get(category, None)
        if category_id is not None:
            for tag in tag_list:
                mapped_tags[tag] = category_id
    return mapped_tags