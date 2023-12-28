import os
import requests
import time
from requests.exceptions import HTTPError, Timeout, ConnectionError
from modelsntebles import Artist, Image, Tag, ImageTags, db

# Tag category mappings
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


class E621Downloader:
    def __init__(self, output_folder='images', download_status=None):
        self.base_url = "https://e621.net/posts.json"
        self.headers = {
            'User-Agent': 'E621Downloader/1.0 (by your_username_on_e621)'
        }
        self.download_status = download_status
        self.output_folder = output_folder
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
        

    def download_image(self, image_url, file_name, artist_key):
        try:
            if os.path.exists(file_name):
                print(f"File {file_name} already exists.")
                return False
            start_time = time.time()
            response = requests.get(image_url, headers=self.headers, stream=True, timeout=60)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0

            with open(file_name, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        downloaded_size += len(chunk)
                        elapsed_time = time.time() - start_time
                        speed = (downloaded_size / 1024) / elapsed_time if elapsed_time > 0 else 0  # KB/s
                        percent_done = (downloaded_size / total_size) * 100
                        self.update_status(artist_key, "Downloading", speed=speed, current_file_percent=percent_done)

            print(f"Downloaded {file_name}")
            return True
        except (HTTPError, Timeout, ConnectionError) as http_err:
            print(f"HTTP error occurred: {http_err}")
            return False
        except Exception as err:
            print(f"An error occurred: {err}")
            return False

    def download_from_id(self, start_id=0, tags="", limit=1000, artist_key=""):
        last_id = start_id
        downloaded_files = 0

        self.update_status(artist_key, "Contacting Back End")
        total_files = 0
        while True:
            url = f"{self.base_url}?tags=id:>={last_id} order:id_asc {tags}&limit={limit}"
            print(f"Downloading from {url}")
            try:
                response = requests.get(url, headers=self.headers, timeout=60)
                response.raise_for_status()
                posts = response.json().get('posts', [])
                if not posts:
                    print("No more posts to download.")
                    break

                total_files += len(posts)
                self.update_status(artist_key, "Downloading", total_files=total_files)

                for post in posts:
                    file_url = post['file']['url']
                    md5 = post['file']['md5']
                    sql_md5_search = Image.query.filter_by(md5=md5).first()
                    if sql_md5_search:
                        print(f"File {md5} already exists in database.")
                        last_id = post['id'] + 1
                        continue
                    if not file_url:
                        last_id = post['id'] + 1
                        continue
                    file_name = os.path.basename(file_url)
                    file_path = os.path.join(self.output_folder, file_name)

                    if self.download_image(file_url, file_path, artist_key):
                        self.save_post_to_db(post)
                    downloaded_files += 1
                    self.update_status(artist_key, "Downloading", downloaded_files=downloaded_files)

                    last_id = post['id'] + 1
                    

            except (HTTPError, Timeout, ConnectionError) as http_err:
                print(f"HTTP error occurred: {http_err}")
            except Exception as err:
                print(f"An error occurred: {err}")

        self.update_status(artist_key, "Finished", downloaded_files=downloaded_files)

    def update_status(self, artist_key, status, downloaded_files=None, total_files=None, speed=0, current_file_percent=0):
        if self.download_status is not None and artist_key in self.download_status:
            self.download_status[artist_key]["status"] = status
            if downloaded_files is not None:
                self.download_status[artist_key]["downloaded_files"] = downloaded_files
            if total_files is not None:
                self.download_status[artist_key]["total_files"] = total_files
            self.download_status[artist_key]["speed"] = f"{speed:.2f} KB/s"
            self.download_status[artist_key]["current_file_percent"] = current_file_percent

    def save_post_to_db(self, post):
        with db.session.begin():
            artist_names = post['tags']['artist']
            for artist_name in artist_names:
                artist = Artist.query.filter_by(name=artist_name).first()
                if not artist:
                    artist = Artist(name=artist_name, from_e621=True)
                    db.session.add(artist)
                else:
                    artist.from_e621 = True
            
            image = Image.query.filter_by(md5=post['file']['md5']).first()
            if not image:
                image = Image(
                    file_name=post['file']['md5'],
                    file_type=f".{post['file']['ext']}",
                    artist_id=artist.id,
                    md5=post['file']['md5'],
                    check_again=False,
                    score=post['score']['total'],
                    scored=True
                )
                db.session.add(image)

            for tag_category, tag_list in post['tags'].items():
                category_id = MAPPING.get(tag_category)
                if category_id is not None:
                    for tag_name in tag_list:
                        print(tag_name)
                        tag = Tag.query.filter_by(tag_name=tag_name).first()
                        if not tag:
                            tag = Tag(tag_name=tag_name, category=category_id)
                            db.session.add(tag)
                        image_tag = ImageTags(image_id=image.id, tag_id=tag.id)
                        db.session.add(image_tag)

            db.session.commit()

# This allows the script to be imported without immediately executing the download process.
if __name__ == "__main__":
    print("This script is intended to be imported as a module, not executed directly.")