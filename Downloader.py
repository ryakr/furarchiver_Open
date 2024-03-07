import os
import re
import requests
import json
import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, unquote
from unidecode import unidecode
from modelsntebles import Artist, Image, Tag, ImageTags, ArtistAlias, Website, db
import hashlib
import time
import threading
import queue
from flask import current_app
from E621_Downloader import E621Downloader

# Constants
BASE_URL = "http://example.onion/fa/"
SOCKS_PROXY_PORT = "9150"
CONFIG_FILE = 'config.json'
PROXIES = {
    'http': f'socks5h://127.0.0.1:{SOCKS_PROXY_PORT}',
    'https': f'socks5h://127.0.0.1:{SOCKS_PROXY_PORT}'
}

# Utility functions
def size_str_to_bytes(size_str):
    size_units = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3}
    
    # Extract the numerical part of the size string
    size_match = re.findall(r'\d+\.\d+|\d+', size_str)

    # Extract the unit part of the size string
    unit_match = re.findall(r'[BKMGT]', size_str)

    # If there is no unit match, assume the unit is bytes ('B')
    unit = unit_match[0] if unit_match else 'B'

    # If there is no size match, return 0
    if not size_match:
        return 0
    
    # Calculate the size in bytes
    return int(float(size_match[0]) * size_units[unit])

def is_size_within_range(size1, size2, range_bytes=1024 * 1024 // 10):
    return abs(size1 - size2) <= range_bytes

def sanitize_filename(filename):
    filename = unquote(unidecode(filename))
    return re.sub(r'[\\/*?:"<>|]', "", filename).split("?")[0]

# Downloader class
class Downloader:
    def __init__(self, base_url, socks_proxy_port, config_file, db_session, FA_FOLDER, app, source='default'):
        self.base_url = base_url
        self.socks_proxy_port = socks_proxy_port
        self.proxies = {
            'http': f'socks5h://127.0.0.1:{socks_proxy_port}',
            'https': f'socks5h://127.0.0.1:{socks_proxy_port}'
        }
        self.config_file = config_file
        self.download_queue = queue.Queue()
        self.queue = []
        self.download_status = {}
        self.db_session = db_session
        self.queueactive = False
        self.app = app
        self.threads = []
        self.threads_active = False
        self.source = source
        self.download_destination = FA_FOLDER
        self.condition = threading.Condition()  # Threading condition
        self._stop_event = threading.Event()
        self.start_threads() 

    def add_artist_to_db(self, artist_name):
        artist = Artist.query.filter_by(name=artist_name).first()
        if artist is None:
            artist = Artist(name=artist_name, from_furaffinity=True)
            self.db_session.add(artist)
            try:
                self.db_session.commit()
            except Exception as e:
                self.db_session.rollback()
                print(f"Error adding artist: {e}")
        return artist
    
    def get_artist_id(self, artist_name):
        artist = Artist.query.filter_by(name=artist_name).first()
        return artist.id if artist else None

    def add_image_to_db(self, file_name, file_type, artist):
        image = Image.query.filter_by(file_name=file_name).first()
        if image is None:
            if os.path.exists(os.path.join(self.download_destination, artist.name, file_name + file_type)):
                bites = os.path.getsize(os.path.join(self.download_destination, artist.name, file_name + file_type))
            else:
                bites = 0
            image = Image(file_name=file_name, file_type=file_type, artist_id=artist.id, checked_count=0, check_again=True, Bytes=bites)
            self.db_session.add(image)
            self.db_session.commit()
        return image
    
    def add_tags_to_db(self, tags, image):
        for tag_name in tags:
            tag = Tag.query.filter_by(tag_name=tag_name).first()
            if tag is None:
                tag = Tag(tag_name=tag_name)
                self.db_session.add(tag)
                self.db_session.commit()
            image.tags.append(tag)
        self.db_session.commit()

    def add_to_queue(self, artist_name, source='default'):
        with self.condition: 
            if artist_name.strip().lower() in self.queue:
                print(f"{artist_name} is already in the queue.")
                return 
            print(f"Added {artist_name} to download queue")
            artist_key = artist_name.strip().lower()
            self.download_queue.put((artist_key,source))  # Add to the thread-safe queue
            self.queue.append(artist_key)  # Keep in status tracking list
            self.download_status[artist_key] = {
                "total_files": 0,
                "downloaded_files": 0,
                "speed": "0 KB/s",
                "status": "pending",
                "current_file_percent": 0.0
            }
            if not self.threads_active:
                self.start_threads()
            self.condition.notify()


    def get_queue(self):
        status_report = []
        queue_length = len(self.queue)

        for artist_key in self.queue[:4]:  # Process only the first 4 artists in the queue
            status = self.download_status.get(artist_key, {"status": "pending"})
            report = {
                'artist_info': artist_key,
                'status': status['status'],
                'downloaded_files': status['downloaded_files'],
                'total_files': status['total_files'],
                'speed': status['speed'],
                'current_file_percent': status['current_file_percent']
            }
            status_report.append(report)

        if queue_length > 4:
            additional_artists = queue_length - 4
            pending_report = {
                'artist_info': 'Additional Artists',
                'status': f'{additional_artists} others pending...',
                'downloaded_files': 0,
                'total_files': 0,
                'speed': 0,
                'current_file_percent': 0
            }
            status_report.append(pending_report)

        return status_report

    def download_artist_wrapped(self, artist_key, download_destination, source):
        # Push the application context
        with self.app.app_context():
            self.download_artist(artist_key, download_destination, source)

    def process_queue(self):
        if not self.threads_active:
            self.start_threads()

        if not self.download_destination:
            print("Download destination is not set.")
            return

        self.queueactive = True

        while not self.download_queue.empty():
            time.sleep(1)  # Wait for the queue to empty

        self.queueactive = False
        self.stop_threads()  # Stop threads after the queue is empty


    def start_threads(self):
        def worker():
            while True:
                if self._stop_event.is_set():
                    break
                with self.condition:  # Acquire the condition lock
                    while self.download_queue.empty():  # Check if queue is empty
                        self.condition.wait()  # Wait for an item to be added
                    artist_key, source = self.download_queue.get()  # Retrieve both artist_key and source

                # Process download outside of the locked section
                self.download_artist_wrapped(artist_key, self.download_destination, source)
                self.download_queue.task_done()
                self.queue.remove(artist_key)

        for _ in range(3):
            thread = threading.Thread(target=worker)
            thread.start()
            self.threads.append(thread)
        self.threads_active = True


    def stop_threads(self, timeout=1):
        # Signal the threads to stop
        self._stop_event.set()

        # Wait for the threads to stop with a timeout and remove them from the list if they have stopped
        active_threads = []
        for thread in self.threads:
            thread.join(timeout)
            if thread.is_alive():
                active_threads.append(thread)

        self.threads = active_threads
        self.threads_active = len(self.threads) > 0


    def download_artist(self, artist_key, destination, source):
        print(source)
        if source.lower() == 'e621':
            print(f"Downloading {artist_key} from e621")
            DL_Path = os.path.join(destination, artist_key)
            e621_dl = E621Downloader(self.db_session, output_folder=DL_Path, download_status=self.download_status)
            e621_dl.download_from_id(start_id=0, tags=artist_key, limit=1000, artist_key=artist_key)
        else:
            tempkey = artist_key.replace('_', '')
            artist_url = urljoin(self.base_url, tempkey)
            print(f"Downloading {artist_key} from {artist_url}")
            self.download_status[artist_key]["status"] = "Contacting Back End"
            response = requests.get(artist_url, proxies=self.proxies, timeout=120)
            print(f"{artist_key}: {response.status_code}")
            if response.status_code == 404:
                print(f"Artist {artist_key} not found.")
                return
            elif response.status_code == 502:
                print(f"Error 502: Bad Gateway, using .onion.ly")
                artist_url = artist_url.replace(".onion/", ".onion.ly/")
                response = requests.get(artist_url, timeout=120)
            elif response.status_code != 200:
                print(f"Error downloading {artist_key}: {response.status_code}")
                return
            self.download_status[artist_key]["status"] = "BackEnd Got"
            artist = self.add_artist_to_db(artist_key)
            soup = BeautifulSoup(response.content, 'html.parser')

            artist_directory = os.path.join(destination, artist_key)
            os.makedirs(artist_directory, exist_ok=True)

            file_links = self.extract_file_links(soup, artist_url)
            total_files = len(file_links)
            self.download_status[artist_key]["total_files"] = total_files
            self.download_status[artist_key]["status"] = "downloading"

            for index, (file_url, date, size_str) in enumerate(file_links, start=1):
                self.download_file(file_url, date, size_str, artist_directory, artist_key, artist)
                self.download_status[artist_key]["downloaded_files"] = index


    def extract_file_links(self, soup, artist_url):
        file_links = []
        for row in soup.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) >= 4:
                link, date_td, size_td = cells[1].find('a', href=True), cells[2], cells[3]
                if link and date_td and size_td:
                    file_url = f"{artist_url}/{link['href']}"
                    file_links.append((file_url, date_td.text.strip(), size_td.text.strip()))
        return file_links

    def download_file(self, file_url, date, size_str, artist_directory, artist_key, artistdb):
        parsed_url = urlparse(file_url)
        sanitized_filename = sanitize_filename(os.path.basename(parsed_url.path))
        if sanitized_filename == "":
            return
        name, extenstion = os.path.splitext(sanitized_filename)
        
        if Image.query.filter_by(file_name=name).first() is not None:
            return
        
        file_path = os.path.join(artist_directory, sanitized_filename)
        file_size_from_website = size_str_to_bytes(size_str)
        if file_size_from_website == 0:
            remoteid = name.split('.')[0]
            tempkey = artist_key.replace('_', '')
            #https://d.furaffinity.net/art/{artist_key}/{remoteid}/{name}{extenstion}
            response2 = requests.get(f"https://d.furaffinity.net/art/{tempkey}/{remoteid}/{name}{extenstion}")
            if response2.status_code == 200:
                start_time = datetime.datetime.now()
                strings = "failure"
                file_size_from_website = int(response2.headers.get('content-length', 0))
                if os.path.exists(file_path):
                    existing_file_size = os.path.getsize(file_path)
                    if is_size_within_range(existing_file_size, file_size_from_website):
                        return
                with open(file_path, 'wb') as file:
                    for data in response2.iter_content(1024):
                        file.write(data)
                        elapsed_time = (datetime.datetime.now() - start_time).total_seconds()
                        downloaded_size = os.path.getsize(file_path)
                        speed = (downloaded_size / 1024) / elapsed_time if elapsed_time > 0 else 0
                        self.download_status[artist_key]["speed"] = f"{speed:.2f} KB/s"
                        self.download_status[artist_key]["current_file_percent"] = int((downloaded_size / file_size_from_website) * 100)
                if os.path.getsize(file_path) == file_size_from_website:
                    if extenstion.lower() in ['.png', '.jpg', '.jpeg', '.gif']:
                        image = self.add_image_to_db(name, extenstion, artistdb)
                        artistdb.images.append(image)
                        self.db_session.commit()
                        strings = "success"
                print(f"Grabbed From d.furaffinity.net {sanitized_filename} | {strings}")
                return
            print(f"Skipping {sanitized_filename} with size 0")
            return
        max_attempts = 3
        attempts = 0
        if extenstion.lower() in ['.png', '.jpg', '.jpeg', '.gif']:
            image = self.add_image_to_db(name, extenstion, artistdb)
            artistdb.images.append(image)
            self.db_session.commit()
        while attempts < max_attempts:
            try:
                headers = {}
                existing_file_size = 0
                if os.path.exists(file_path):
                    existing_file_size = os.path.getsize(file_path)
                    if is_size_within_range(existing_file_size, file_size_from_website):
                        return
                    headers['Range'] = f'bytes={existing_file_size}-'

                response = requests.get(file_url, headers=headers, proxies=self.proxies, stream=True)

                if response.status_code == 416:  # 'Range Not Satisfiable'
                    os.remove(file_path)
                    existing_file_size = 0
                    headers.pop('Range', None)
                    response = requests.get(file_url, headers=headers, proxies=self.proxies, stream=True)
                
                if response.status_code != 200 and response.status_code != 206:
                    print(f"Error downloading {sanitized_filename}: {response.status_code}")
                    attempts += 1
                    continue

                total_size = int(response.headers.get('content-length', 0)) + existing_file_size
                start_time = datetime.datetime.now()
                with open(file_path, 'ab' if existing_file_size else 'wb') as file:
                    for data in response.iter_content(1024):
                        file.write(data)
                        elapsed_time = (datetime.datetime.now() - start_time).total_seconds()
                        downloaded_size = os.path.getsize(file_path) - existing_file_size
                        speed = (downloaded_size / 1024) / elapsed_time if elapsed_time > 0 else 0  # KB/s
                        self.download_status[artist_key]["speed"] = f"{speed:.2f} KB/s"
                        self.download_status[artist_key]["current_file_percent"] = int((downloaded_size / total_size) * 100)

                if os.path.getsize(file_path) == total_size:
                    self.set_file_timestamp(file_path, date)
                    if extenstion.lower() in ['.png', '.jpg', '.jpeg', '.gif']:
                        image = Image.query.filter_by(file_name=name).first()
                        md5hash = hashlib.md5(open(file_path,'rb').read() ).hexdigest()
                        print(md5hash)
                        image.md5 = md5hash
                        self.db_session.commit()
                    break
                else:
                    attempts += 1
            except ConnectionError as e:
                if 'SOCKSHTTPConnectionPool' in str(e):
                    print("Caught a SOCKSHTTPConnectionPool error, sleeping for 2 seconds")
                    time.sleep(2)
                    print("Retrying download")
                    attempts += 1
                else:
                    print("Caught a different ConnectionError")
            except Exception as e:
                print(f"Error downloading {sanitized_filename}: {e}")
                attempts += 1

        if attempts == max_attempts:
            print(f"Failed to download {sanitized_filename} after {max_attempts} attempts.")

    def set_file_timestamp(self, file_path, date_str):
        date_time_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d %H:%M')
        os.utime(file_path, (date_time_obj.timestamp(), date_time_obj.timestamp()))

# Main function
def main():
    downloader = Downloader(BASE_URL, PROXIES)

    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as file:
            config = json.load(file)
            artist_names = config.get('artist_names', '').split(',')
            download_destination = config.get('download_destination', '')

    if not artist_names or not download_destination:
        artist_names = input("Enter artist names (comma separated): ").split(',')
        download_destination = input("Enter download destination path: ")

    for artist in artist_names:
        downloader.add_to_queue(artist)

    downloader.process_queue(download_destination)

    print("Download Queue Status:")
    print(downloader.get_queue())

if __name__ == "__main__":
    main()
