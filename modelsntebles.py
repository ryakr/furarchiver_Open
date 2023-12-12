from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Artist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    other_names = db.relationship('ArtistAlias', backref='artist', lazy=True)
    websites = db.relationship('Website', backref='artist', lazy=True)
    images = db.relationship('Image', backref='artist', lazy=True)

class Image(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    file_name = db.Column(db.String(120))
    file_type = db.Column(db.String(50))
    artist_id = db.Column(db.Integer, db.ForeignKey('artist.id'))
    md5 = db.Column(db.String(32))
    tags = db.relationship('Tag', secondary='image_tags', back_populates='images')
    score = db.Column(db.Float, default=0.0)
    scored = db.Column(db.Boolean, default=False)
    checked_count = db.Column(db.Integer, default=0)
    check_again = db.Column(db.Boolean, default=True)

class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tag_name = db.Column(db.String(50))
    category = db.Column(db.Integer)
    count = db.Column(db.Integer)
    images = db.relationship('Image', secondary='image_tags', back_populates='tags')

class ImageTags(db.Model):
    __tablename__ = 'image_tags'
    image_id = db.Column(db.Integer, db.ForeignKey('image.id'), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('tag.id'), primary_key=True)

class ArtistAlias(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    artist_id = db.Column(db.Integer, db.ForeignKey('artist.id'))
    alias = db.Column(db.String(100))

class Website(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    artist_id = db.Column(db.Integer, db.ForeignKey('artist.id'))
    website_url = db.Column(db.String(200))