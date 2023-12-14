from flask import Flask
from modelsntebles import db, Tag
import csv

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///FurArchiver.db'
db.init_app(app)

@app.route('/')
def import_tags_from_csv():
    """ Import tags from a CSV file into the database after checking for existing tags. """
    csv_file_path = r'tags-2023-12-11.csv'

    # Retrieve existing tag names
    existing_tags = {tag.tag_name for tag in Tag.query.with_entities(Tag.tag_name).all()}
    print(f"Found {len(existing_tags)} existing tags")
    # Load CSV file and filter out existing tags
    new_tags = []
    with open(csv_file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        i = 0
        for row in reader:
            i += 1
            if row['name'] not in existing_tags:
                new_tags.append({'tag_name': row['name'], 'category': int(row['category']), 'count': 0})
            print(f"Processed {i} rows")

    # Bulk insert new tags
    print(f"Found {len(new_tags)} new tags")
    if new_tags:
        db.session.bulk_insert_mappings(Tag, new_tags)
        print("Committing changes...")
        db.session.commit()
        return f"Imported {len(new_tags)} new tags"
    else:
        return "No new tags to import"

if __name__ == '__main__':
    app.run()
