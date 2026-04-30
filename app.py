from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import psycopg2
import psycopg2.extras
import os
import subprocess
import threading
import uuid
from datetime import datetime
import cloudinary
import cloudinary.uploader
import cloudinary.api

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pressjobs_secret_2024')

_DATA_DIR = os.environ.get('DATA_DIR', '.')
UPLOAD_FOLDER = os.path.join(_DATA_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join('static', 'uploads'), exist_ok=True)

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set.")

# ── Cloudinary configuration ──────────────────────────────────────────────────
cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET'),
    secure=True
)

_bg_jobs      = {}
_bg_jobs_lock = threading.Lock()


# ── Cloudinary helpers ────────────────────────────────────────────────────────

def cloudinary_upload(file_storage, resource_type='auto', folder='pressjobs'):
    """Upload a FileStorage object to Cloudinary and return the secure URL."""
    try:
        result = cloudinary.uploader.upload(
            file_storage,
            resource_type=resource_type,
            folder=folder
        )
        return result.get('secure_url')
    except Exception as e:
        print(f"Cloudinary upload error: {e}")
        return None


def cloudinary_upload_path(file_path, resource_type='auto', folder='pressjobs'):
    """Upload a local file path to Cloudinary and return the secure URL."""
    try:
        result = cloudinary.uploader.upload(
            file_path,
            resource_type=resource_type,
            folder=folder
        )
        return result.get('secure_url')
    except Exception as e:
        print(f"Cloudinary upload error: {e}")
        return None


def cloudinary_delete_by_url(url):
    """Delete a Cloudinary asset by its URL (best-effort, silent on error)."""
    if not url or 'cloudinary.com' not in url:
        return
    try:
        # Extract public_id from URL:
        # e.g. https://res.cloudinary.com/<cloud>/image/upload/v123/pressjobs/filename
        parts = url.split('/upload/')
        if len(parts) == 2:
            public_id_with_ext = parts[1]
            # Strip version segment if present (v123/)
            if public_id_with_ext.startswith('v') and '/' in public_id_with_ext:
                public_id_with_ext = public_id_with_ext.split('/', 1)[1]
            # Strip extension
            public_id = os.path.splitext(public_id_with_ext)[0]
            cloudinary.uploader.destroy(public_id, resource_type='auto')
    except Exception:
        pass


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        account_type TEXT NOT NULL,
        bio TEXT,
        location TEXT,
        skills TEXT,
        education TEXT,
        experience TEXT,
        profile_image TEXT,
        last_name TEXT,
        gender TEXT,
        civil_status TEXT,
        specialty TEXT,
        years_experience TEXT,
        preferred_channels TEXT,
        extra_skills TEXT,
        cv_filename TEXT,
        intro_video TEXT,
        article_links TEXT,
        channel_type TEXT,
        cover_image TEXT,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id SERIAL PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        category TEXT NOT NULL,
        location TEXT,
        job_type TEXT,
        salary TEXT,
        requirements TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(channel_id) REFERENCES users(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS applications (
        id SERIAL PRIMARY KEY,
        job_id INTEGER NOT NULL,
        journalist_id INTEGER NOT NULL,
        message TEXT,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(journalist_id) REFERENCES users(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL,
        title TEXT,
        description TEXT,
        media_filename TEXT,
        media_type TEXT,
        post_type TEXT DEFAULT 'media',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')
    conn.commit()
    conn.close()


init_db()

ADMIN_EMAIL    = os.environ.get('ADMIN_EMAIL', 'admin@pressjobs.dz')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Admin@2024!')


def ensure_admin():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id FROM users WHERE email=%s', (ADMIN_EMAIL,))
    exists = c.fetchone()
    if not exists:
        c.execute(
            '''INSERT INTO users (name, email, password, account_type, is_admin)
               VALUES (%s,%s,%s,%s,1)''',
            ('مدير النظام', ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), 'admin')
        )
        conn.commit()
    else:
        c.execute('UPDATE users SET is_admin=1 WHERE email=%s', (ADMIN_EMAIL,))
        conn.commit()
    conn.close()


ensure_admin()


# ── File serving (local fallback, mostly unused when Cloudinary is active) ────

from flask import send_from_directory

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    data_uploads = os.path.join(_DATA_DIR, 'uploads')
    if os.path.exists(os.path.join(data_uploads, filename)):
        return send_from_directory(data_uploads, filename)
    return send_from_directory(os.path.join('static', 'uploads'), filename)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('profile', user_id=session['user_id']))
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT jobs.*, users.name as channel_name, users.location as channel_location
        FROM jobs JOIN users ON jobs.channel_id = users.id
        ORDER BY jobs.created_at DESC LIMIT 6
    ''')
    jobs = c.fetchall()
    c.execute("SELECT * FROM users WHERE account_type='channel' LIMIT 4")
    channels = c.fetchall()
    c.execute("SELECT * FROM users WHERE account_type='journalist' LIMIT 6")
    journalists = c.fetchall()
    conn.close()
    return render_template('index.html', jobs=jobs, channels=channels, journalists=journalists)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('profile', user_id=session['user_id']))
    if request.method == 'POST':
        name         = request.form['name']
        email        = request.form['email']
        password     = generate_password_hash(request.form['password'])
        account_type = request.form['account_type']
        location     = request.form.get('location', '')
        bio          = request.form.get('bio', '')
        skills       = request.form.get('skills', '')
        education    = request.form.get('education', '')
        specialty    = request.form.get('specialty', '')
        channel_type = request.form.get('channel_type', '')
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute('''INSERT INTO users (name, email, password, account_type, location, bio, skills, education, specialty, channel_type)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                      (name, email, password, account_type, location, bio, skills, education, specialty, channel_type))
            conn.commit()
            conn.close()
            flash('تم إنشاء حسابك بنجاح! يمكنك تسجيل الدخول الآن', 'success')
            return redirect(url_for('login'))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            conn.close()
            flash('البريد الإلكتروني مستخدم بالفعل', 'error')
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email    = request.form['email']
        password = request.form['password']
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE email=%s', (email,))
        user = c.fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id']      = user['id']
            session['user_name']    = user['name']
            session['account_type'] = user['account_type']
            flash(f'مرحباً {user["name"]}!', 'success')
            return redirect(url_for('profile', user_id=user['id']))
        flash('بيانات الدخول غير صحيحة', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('profile', user_id=session['user_id']))


@app.route('/job/<int:job_id>/edit', methods=['POST'])
def edit_job(job_id):
    if 'user_id' not in session or session.get('account_type') != 'channel':
        return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM jobs WHERE id=%s AND channel_id=%s', (job_id, session['user_id']))
    job = c.fetchone()
    if not job:
        conn.close()
        flash('غير مصرح لك بتعديل هذه الوظيفة', 'error')
        return redirect(url_for('profile', user_id=session['user_id']))
    c.execute('''UPDATE jobs SET title=%s, description=%s, category=%s, location=%s, job_type=%s, salary=%s, requirements=%s
                 WHERE id=%s''',
              (request.form['title'], request.form['description'], request.form['category'],
               request.form.get('location',''), request.form.get('job_type',''),
               request.form.get('salary',''), request.form.get('requirements',''), job_id))
    conn.commit()
    conn.close()
    flash('تم تعديل الوظيفة بنجاح!', 'success')
    return redirect(url_for('profile', user_id=session['user_id']))


@app.route('/job/<int:job_id>/delete')
def delete_job(job_id):
    if 'user_id' not in session or session.get('account_type') != 'channel':
        return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM jobs WHERE id=%s AND channel_id=%s', (job_id, session['user_id']))
    job = c.fetchone()
    if job:
        c.execute('DELETE FROM applications WHERE job_id=%s', (job_id,))
        c.execute('DELETE FROM jobs WHERE id=%s', (job_id,))
        conn.commit()
        flash('تم حذف الوظيفة نهائياً', 'success')
    conn.close()
    return redirect(url_for('profile', user_id=session['user_id']))


@app.route('/jobs')
def jobs():
    category = request.args.get('category', '')
    search   = request.args.get('search', '')
    conn = get_db()
    c = conn.cursor()
    query = '''SELECT jobs.*, users.name as channel_name FROM jobs
               JOIN users ON jobs.channel_id = users.id WHERE 1=1'''
    params = []
    if category:
        query += ' AND jobs.category=%s'
        params.append(category)
    if search:
        query += ' AND (jobs.title ILIKE %s OR jobs.description ILIKE %s)'
        params.extend([f'%{search}%', f'%{search}%'])
    query += ' ORDER BY jobs.created_at DESC'
    c.execute(query, params)
    all_jobs = c.fetchall()
    conn.close()
    return render_template('jobs.html', jobs=all_jobs, category=category, search=search)


@app.route('/job/<int:job_id>')
def job_detail(job_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT jobs.*, users.name as channel_name, users.bio as channel_bio, users.location as channel_location
                 FROM jobs JOIN users ON jobs.channel_id = users.id WHERE jobs.id=%s''', (job_id,))
    job = c.fetchone()
    conn.close()
    if not job:
        return redirect(url_for('jobs'))
    return render_template('job_detail.html', job=job)


@app.route('/apply/<int:job_id>', methods=['POST'])
def apply(job_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    message = request.form.get('message', '')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM applications WHERE job_id=%s AND journalist_id=%s',
              (job_id, session['user_id']))
    existing = c.fetchone()
    if not existing:
        c.execute('INSERT INTO applications (job_id, journalist_id, message) VALUES (%s,%s,%s)',
                  (job_id, session['user_id'], message))
        conn.commit()
        flash('تم إرسال طلبك بنجاح!', 'success')
    else:
        flash('لقد تقدمت لهذه الوظيفة مسبقاً', 'warning')
    conn.close()
    return redirect(url_for('job_detail', job_id=job_id))


@app.route('/post-job', methods=['GET', 'POST'])
def post_job():
    if 'user_id' not in session or session['account_type'] != 'channel':
        return redirect(url_for('login'))
    if request.method == 'POST':
        conn = get_db()
        c = conn.cursor()
        c.execute('''INSERT INTO jobs (channel_id, title, description, category, location, job_type, salary, requirements)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                  (session['user_id'], request.form['title'], request.form['description'],
                   request.form['category'], request.form['location'], request.form['job_type'],
                   request.form.get('salary',''), request.form.get('requirements','')))
        conn.commit()
        conn.close()
        flash('تم نشر الوظيفة بنجاح!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('post_job.html')


@app.route('/profile/<int:user_id>')
def profile(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id=%s', (user_id,))
    user = c.fetchone()
    if not user:
        conn.close()
        flash('المستخدم غير موجود', 'error')
        return redirect(url_for('index'))

    if user['account_type'] == 'channel':
        c.execute('SELECT * FROM jobs WHERE channel_id=%s ORDER BY created_at DESC', (user_id,))
        jobs = c.fetchall()
        c.execute('''
            SELECT applications.*, jobs.title AS job_title,
                   u.name AS applicant_name, u.profile_image AS applicant_image,
                   u.skills, u.education, u.location AS applicant_location,
                   u.years_experience, u.specialty, u.cv_filename
            FROM applications
            JOIN jobs ON applications.job_id = jobs.id
            JOIN users u ON applications.journalist_id = u.id
            WHERE jobs.channel_id = %s
            ORDER BY applications.created_at DESC
        ''', (user_id,))
        applications = c.fetchall()
        total_applications = len(applications)
        accepted_count = sum(1 for a in applications if a['status'] == 'accepted')
        job_categories = list({j['category'] for j in jobs})
        conn.close()
        return render_template('channel_profile.html',
            channel=user, jobs=jobs, applications=applications,
            total_applications=total_applications, accepted_count=accepted_count,
            job_categories=job_categories)

    c.execute('SELECT * FROM posts WHERE user_id=%s ORDER BY created_at DESC', (user_id,))
    posts = c.fetchall()
    conn.close()
    return render_template('profile.html', user=user, jobs=None, posts=posts)


@app.route('/profile/<int:user_id>/update', methods=['POST'])
def update_profile(user_id):
    if 'user_id' not in session or session['user_id'] != user_id:
        return redirect(url_for('login'))

    name              = request.form.get('name', '').strip()
    last_name         = request.form.get('last_name', '').strip()
    location          = request.form.get('location', '').strip()
    bio               = request.form.get('bio', '').strip()
    gender            = request.form.get('gender', '').strip()
    civil_status      = request.form.get('civil_status', '').strip()
    specialty         = request.form.get('specialty', '').strip()
    years_experience  = request.form.get('years_experience', '').strip()
    education         = request.form.get('education', '').strip()
    skills            = request.form.get('skills', '').strip()
    preferred_channels= request.form.get('preferred_channels', '').strip()
    extra_skills      = request.form.get('extra_skills', '').strip()
    article_links     = request.form.get('article_links', '').strip()

    ALL_VIDEO = {'.mp4','.webm','.mov','.avi','.mkv','.flv','.wmv','.m4v','.3gp','.ogv','.ts','.mts','.m2ts'}

    def upload_file_to_cloudinary(field, allowed, resource_type='auto'):
        """Upload a form file field to Cloudinary, return the URL or None."""
        if field not in request.files:
            return None
        f = request.files[field]
        if not f or not f.filename:
            return None
        ext = os.path.splitext(secure_filename(f.filename))[1].lower()
        if ext not in allowed:
            return None
        url = cloudinary_upload(f, resource_type=resource_type)
        return url

    cv_url     = upload_file_to_cloudinary('cv_file',    {'.pdf', '.doc', '.docx'}, resource_type='raw')
    intro_url  = upload_file_to_cloudinary('intro_video', ALL_VIDEO, resource_type='video')

    # Fallback: processed intro video already on disk → upload to Cloudinary
    if not intro_url:
        processed_intro = request.form.get('processed_intro_video', '').strip()
        if processed_intro:
            safe_pi = secure_filename(processed_intro)
            local_path = os.path.join(UPLOAD_FOLDER, safe_pi)
            if os.path.exists(local_path):
                intro_url = cloudinary_upload_path(local_path, resource_type='video')

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT cv_filename, intro_video FROM users WHERE id=%s', (user_id,))
    old = c.fetchone()

    # Delete old Cloudinary assets when replaced
    if cv_url and old and old['cv_filename']:
        cloudinary_delete_by_url(old['cv_filename'])
    if intro_url and old and old['intro_video']:
        cloudinary_delete_by_url(old['intro_video'])

    set_parts = [
        'name=%s', 'last_name=%s', 'location=%s', 'bio=%s', 'gender=%s', 'civil_status=%s',
        'specialty=%s', 'years_experience=%s', 'education=%s', 'skills=%s',
        'preferred_channels=%s', 'extra_skills=%s', 'article_links=%s'
    ]
    values = [name, last_name, location, bio, gender, civil_status,
              specialty, years_experience, education, skills,
              preferred_channels, extra_skills, article_links]

    if cv_url:
        set_parts.append('cv_filename=%s')
        values.append(cv_url)
    if intro_url:
        set_parts.append('intro_video=%s')
        values.append(intro_url)
    values.append(user_id)

    c.execute(f'UPDATE users SET {", ".join(set_parts)} WHERE id=%s', values)
    conn.commit()
    conn.close()

    session['user_name'] = name
    flash('تم تحديث معلومات الملف الشخصي بنجاح!', 'success')
    return redirect(url_for('profile', user_id=user_id))


@app.route('/profile/<int:user_id>/upload-picture', methods=['POST'])
def upload_picture(user_id):
    if 'user_id' not in session or session['user_id'] != user_id:
        return redirect(url_for('login'))
    if 'profile_image' not in request.files:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=user_id))
    file = request.files['profile_image']
    if not file or not file.filename:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=user_id))
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif'}
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in IMAGE_EXT:
        flash('صيغة الصورة غير مدعومة', 'error')
        return redirect(url_for('profile', user_id=user_id))

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT profile_image FROM users WHERE id=%s', (user_id,))
    old = c.fetchone()
    if old and old['profile_image']:
        cloudinary_delete_by_url(old['profile_image'])

    url = cloudinary_upload(file, resource_type='image')
    if not url:
        flash('فشل رفع الصورة، حاول مرة أخرى', 'error')
        conn.close()
        return redirect(url_for('profile', user_id=user_id))

    c.execute('UPDATE users SET profile_image=%s WHERE id=%s', (url, user_id))
    conn.commit()
    conn.close()
    flash('تم تحديث صورة الملف الشخصي بنجاح!', 'success')
    return redirect(url_for('profile', user_id=user_id))


@app.route('/profile/<int:user_id>/post', methods=['POST'])
def create_post(user_id):
    if 'user_id' not in session or session['user_id'] != user_id:
        return redirect(url_for('login'))

    title       = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    media_url      = None
    media_type     = None

    VIDEO_EXT = {'.mp4','.webm','.mov','.avi','.mkv','.flv','.wmv','.m4v','.3gp','.ogv','.ts','.mts','.m2ts'}
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif','.svg'}

    # Processed (background-replaced) video already on disk → upload to Cloudinary
    processed_video = request.form.get('processed_video', '').strip()
    if processed_video:
        safe_name = secure_filename(processed_video)
        local_path = os.path.join(UPLOAD_FOLDER, safe_name)
        if os.path.exists(local_path):
            media_url  = cloudinary_upload_path(local_path, resource_type='video')
            media_type = 'video'

    if not media_url and 'media' in request.files:
        file = request.files['media']
        if file and file.filename:
            ext = os.path.splitext(secure_filename(file.filename))[1].lower()
            if ext in VIDEO_EXT:
                media_type = 'video'
                media_url  = cloudinary_upload(file, resource_type='video')
            elif ext in IMAGE_EXT:
                media_type = 'image'
                media_url  = cloudinary_upload(file, resource_type='image')
            else:
                media_type = 'other'
                media_url  = cloudinary_upload(file, resource_type='raw')

    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO posts (user_id, title, description, media_filename, media_type)
                 VALUES (%s, %s, %s, %s, %s)''',
              (user_id, title, description, media_url, media_type))
    conn.commit()
    conn.close()
    flash('تم نشر المحتوى بنجاح!', 'success')
    return redirect(url_for('profile', user_id=user_id))


@app.route('/channel/<int:channel_id>/update', methods=['POST'])
def update_channel(channel_id):
    if 'user_id' not in session or session['user_id'] != channel_id:
        return redirect(url_for('login'))
    name     = request.form.get('name', '').strip()
    location = request.form.get('location', '').strip()
    bio      = request.form.get('bio', '').strip()
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE users SET name=%s, location=%s, bio=%s WHERE id=%s',
              (name, location, bio, channel_id))
    conn.commit()
    conn.close()
    session['user_name'] = name
    flash('تم تحديث معلومات القناة بنجاح!', 'success')
    return redirect(url_for('profile', user_id=channel_id))


@app.route('/channel/<int:channel_id>/upload-picture', methods=['POST'])
def upload_channel_picture(channel_id):
    if 'user_id' not in session or session['user_id'] != channel_id:
        return redirect(url_for('login'))
    if 'profile_image' not in request.files:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    file = request.files['profile_image']
    if not file or not file.filename:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif'}
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in IMAGE_EXT:
        flash('صيغة الصورة غير مدعومة', 'error')
        return redirect(url_for('profile', user_id=channel_id))

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT profile_image FROM users WHERE id=%s', (channel_id,))
    old = c.fetchone()
    if old and old['profile_image']:
        cloudinary_delete_by_url(old['profile_image'])

    url = cloudinary_upload(file, resource_type='image')
    if not url:
        flash('فشل رفع الصورة، حاول مرة أخرى', 'error')
        conn.close()
        return redirect(url_for('profile', user_id=channel_id))

    c.execute('UPDATE users SET profile_image=%s WHERE id=%s', (url, channel_id))
    conn.commit()
    conn.close()
    flash('تم تحديث شعار القناة بنجاح!', 'success')
    return redirect(url_for('profile', user_id=channel_id))


@app.route('/channel/<int:channel_id>/upload-cover', methods=['POST'])
def upload_channel_cover(channel_id):
    if 'user_id' not in session or session['user_id'] != channel_id:
        return redirect(url_for('login'))
    if 'cover_image' not in request.files:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    file = request.files['cover_image']
    if not file or not file.filename:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif'}
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in IMAGE_EXT:
        flash('صيغة الصورة غير مدعومة', 'error')
        return redirect(url_for('profile', user_id=channel_id))

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT cover_image FROM users WHERE id=%s', (channel_id,))
    old = c.fetchone()
    if old and old['cover_image']:
        cloudinary_delete_by_url(old['cover_image'])

    url = cloudinary_upload(file, resource_type='image')
    if not url:
        flash('فشل رفع الصورة، حاول مرة أخرى', 'error')
        conn.close()
        return redirect(url_for('profile', user_id=channel_id))

    c.execute('UPDATE users SET cover_image=%s WHERE id=%s', (url, channel_id))
    conn.commit()
    conn.close()
    flash('تم تحديث صورة الغلاف بنجاح!', 'success')
    return redirect(url_for('profile', user_id=channel_id))


@app.route('/channels')
def channels():
    conn = get_db()
    c = conn.cursor()
    search       = request.args.get('search', '')
    channel_type = request.args.get('type', '')
    query  = "SELECT * FROM users WHERE account_type='channel'"
    params = []
    if channel_type:
        query += ' AND channel_type=%s'
        params.append(channel_type)
    if search:
        query += ' AND (name ILIKE %s OR location ILIKE %s OR bio ILIKE %s)'
        params += [f'%{search}%', f'%{search}%', f'%{search}%']
    query += ' ORDER BY created_at DESC'
    c.execute(query, params)
    all_channels = c.fetchall()
    job_counts = {}
    for ch in all_channels:
        c.execute('SELECT COUNT(*) FROM jobs WHERE channel_id=%s', (ch['id'],))
        job_counts[ch['id']] = c.fetchone()['count']
    conn.close()
    return render_template('channels.html', channels=all_channels, job_counts=job_counts,
                           search=search, channel_type=channel_type)


@app.route('/journalists')
def journalists():
    conn = get_db()
    c = conn.cursor()
    category = request.args.get('category', '')
    search   = request.args.get('search', '')
    query  = "SELECT * FROM users WHERE account_type='journalist'"
    params = []
    if category:
        query += ' AND specialty ILIKE %s'
        params.append(f'%{category}%')
    if search:
        query += ' AND (name ILIKE %s OR skills ILIKE %s OR location ILIKE %s)'
        params += [f'%{search}%', f'%{search}%', f'%{search}%']
    query += ' ORDER BY created_at DESC'
    c.execute(query, params)
    users = c.fetchall()
    conn.close()
    return render_template('journalists.html', journalists=users, category=category, search=search)


@app.route('/update-application/<int:app_id>/<status>')
def update_application(app_id, status):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE applications SET status=%s WHERE id=%s', (status, app_id))
    conn.commit()
    conn.close()
    flash('تم تحديث حالة الطلب', 'success')
    return redirect(url_for('profile', user_id=session['user_id']))


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            flash('غير مصرح لك بالدخول إلى لوحة التحكم', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        email    = request.form['email']
        password = request.form['password']
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE email=%s AND is_admin=1', (email,))
        user = c.fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            session['is_admin']  = True
            flash('مرحباً بك في لوحة التحكم', 'success')
            return redirect(url_for('admin_dashboard'))
        flash('بيانات الدخول غير صحيحة أو ليس لديك صلاحية الوصول', 'error')
    return render_template('admin_login.html')


@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE account_type='journalist'")
    total_journalists = c.fetchone()['count']
    c.execute("SELECT COUNT(*) FROM users WHERE account_type='channel'")
    total_channels = c.fetchone()['count']
    c.execute("SELECT COUNT(*) FROM jobs")
    total_jobs = c.fetchone()['count']
    c.execute("SELECT COUNT(*) FROM applications")
    total_apps = c.fetchone()['count']

    c.execute("SELECT * FROM users WHERE account_type='journalist' ORDER BY created_at DESC")
    journalists = c.fetchall()
    c.execute("SELECT * FROM users WHERE account_type='channel' ORDER BY created_at DESC")
    channels = c.fetchall()

    job_counts = {}
    for ch in channels:
        c.execute('SELECT COUNT(*) FROM jobs WHERE channel_id=%s', (ch['id'],))
        job_counts[ch['id']] = c.fetchone()['count']

    app_counts = {}
    for j in journalists:
        c.execute('SELECT COUNT(*) FROM applications WHERE journalist_id=%s', (j['id'],))
        app_counts[j['id']] = c.fetchone()['count']

    c.execute('''SELECT jobs.*, users.name as channel_name
                 FROM jobs JOIN users ON jobs.channel_id = users.id
                 ORDER BY jobs.created_at DESC LIMIT 10''')
    recent_jobs = c.fetchall()
    conn.close()

    return render_template('admin_dashboard.html',
        total_journalists=total_journalists,
        total_channels=total_channels,
        total_jobs=total_jobs,
        total_apps=total_apps,
        journalists=journalists,
        channels=channels,
        job_counts=job_counts,
        app_counts=app_counts,
        recent_jobs=recent_jobs,
    )


@app.route('/admin/delete/journalist/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_journalist(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id=%s AND account_type=%s', (user_id, 'journalist'))
    user = c.fetchone()
    if not user:
        flash('الصحفي غير موجود', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))

    # Delete Cloudinary assets
    for field in ('profile_image', 'cv_filename', 'intro_video'):
        cloudinary_delete_by_url(user.get(field))

    c.execute('SELECT media_filename FROM posts WHERE user_id=%s', (user_id,))
    posts = c.fetchall()
    for p in posts:
        cloudinary_delete_by_url(p.get('media_filename'))

    c.execute('DELETE FROM applications WHERE journalist_id=%s', (user_id,))
    c.execute('DELETE FROM posts WHERE user_id=%s', (user_id,))
    c.execute('DELETE FROM users WHERE id=%s', (user_id,))
    conn.commit()
    conn.close()
    flash(f'تم حذف حساب الصحفي "{user["name"]}" بنجاح', 'success')
    return redirect(url_for('admin_dashboard') + '#journalists')


@app.route('/admin/delete/channel/<int:channel_id>', methods=['POST'])
@admin_required
def admin_delete_channel(channel_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id=%s AND account_type=%s', (channel_id, 'channel'))
    channel = c.fetchone()
    if not channel:
        flash('القناة غير موجودة', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))

    cloudinary_delete_by_url(channel.get('profile_image'))
    cloudinary_delete_by_url(channel.get('cover_image'))

    c.execute('SELECT id FROM jobs WHERE channel_id=%s', (channel_id,))
    jobs = c.fetchall()
    for job in jobs:
        c.execute('DELETE FROM applications WHERE job_id=%s', (job['id'],))
    c.execute('DELETE FROM jobs WHERE channel_id=%s', (channel_id,))
    c.execute('DELETE FROM users WHERE id=%s', (channel_id,))
    conn.commit()
    conn.close()
    flash(f'تم حذف حساب القناة "{channel["name"]}" وجميع وظائفها بنجاح', 'success')
    return redirect(url_for('admin_dashboard') + '#channels')


@app.route('/admin/delete/job/<int:job_id>', methods=['POST'])
@admin_required
def admin_delete_job(job_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM applications WHERE job_id=%s', (job_id,))
    c.execute('DELETE FROM jobs WHERE id=%s', (job_id,))
    conn.commit()
    conn.close()
    flash('تم حذف الوظيفة بنجاح', 'success')
    return redirect(url_for('admin_dashboard') + '#jobs')


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


# ── Background-replacement routes ─────────────────────────────────────────────

@app.route('/profile/<int:user_id>/process-intro-bg', methods=['POST'])
def process_intro_bg(user_id):
    if 'user_id' not in session or session['user_id'] != user_id:
        return jsonify({'error': 'غير مصرح'}), 401
    return _handle_process_bg()


@app.route('/profile/<int:user_id>/confirm-intro-bg', methods=['POST'])
def confirm_intro_bg(user_id):
    if 'user_id' not in session or session['user_id'] != user_id:
        return jsonify({'error': 'غير مصرح'}), 401

    filename = request.json.get('filename', '').strip()
    if not filename:
        return jsonify({'error': 'اسم الملف مفقود'}), 400

    safe = secure_filename(filename)
    local_path = os.path.join(UPLOAD_FOLDER, safe)
    if not os.path.exists(local_path):
        return jsonify({'error': 'الملف المعالج غير موجود'}), 404

    # Upload processed video to Cloudinary
    url = cloudinary_upload_path(local_path, resource_type='video')
    if not url:
        return jsonify({'error': 'فشل رفع الفيديو إلى Cloudinary'}), 500

    # Clean up local temp file
    try:
        os.remove(local_path)
    except Exception:
        pass

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT intro_video FROM users WHERE id=%s', (user_id,))
    old = c.fetchone()
    if old and old['intro_video']:
        cloudinary_delete_by_url(old['intro_video'])

    c.execute('UPDATE users SET intro_video=%s WHERE id=%s', (url, user_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'url': url})


@app.route('/api/process-bg', methods=['POST'])
def api_process_bg():
    if 'user_id' not in session:
        return jsonify({'error': 'غير مصرح'}), 401
    return _handle_process_bg()


def _handle_process_bg():
    user_id   = session['user_id']
    IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp'}
    ALL_VIDEO = {'.mp4', '.webm', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v', '.3gp', '.ogv'}

    studio_file = request.files.get('studio_image')
    if not studio_file or not studio_file.filename:
        return jsonify({'error': 'الرجاء رفع صورة الخلفية'}), 400
    sext = os.path.splitext(secure_filename(studio_file.filename))[1].lower()
    if sext not in IMAGE_EXT:
        return jsonify({'error': 'صيغة صورة الخلفية غير مدعومة (JPG/PNG/WebP فقط)'}), 400

    ts          = int(datetime.utcnow().timestamp())
    studio_name = f"studio_tmp_{user_id}_{ts}{sext}"
    studio_path = os.path.join(UPLOAD_FOLDER, studio_name)
    studio_file.save(studio_path)

    video_file = request.files.get('video')
    if not video_file or not video_file.filename:
        return jsonify({'error': 'الرجاء اختيار فيديو'}), 400
    vext = os.path.splitext(secure_filename(video_file.filename))[1].lower()
    if vext not in ALL_VIDEO:
        return jsonify({'error': 'صيغة الفيديو غير مدعومة'}), 400

    task_id      = str(uuid.uuid4())
    fg_filename  = f"fg_{user_id}_{ts}{vext}"
    out_filename = f"bgout_{user_id}_{ts}.mp4"
    fg_path      = os.path.join(UPLOAD_FOLDER, fg_filename)
    out_path     = os.path.join(UPLOAD_FOLDER, out_filename)
    video_file.save(fg_path)

    with _bg_jobs_lock:
        _bg_jobs[task_id] = {
            'status':     'queued',
            'progress':   0,
            'result':     None,
            'fg':         fg_filename,
            'studio_tmp': studio_name,
        }

    threading.Thread(
        target=_run_bg_replacement,
        args=(task_id, fg_path, studio_path, out_path),
        daemon=True
    ).start()
    return jsonify({'task_id': task_id})


@app.route('/api/process-bg/status/<task_id>')
def api_process_bg_status(task_id):
    with _bg_jobs_lock:
        job = dict(_bg_jobs.get(task_id, {}))
    if not job:
        return jsonify({'error': 'مهمة غير موجودة'}), 404
    return jsonify(job)


def _run_bg_replacement(task_id, fg_path, studio_path, output_path):
    try:
        with _bg_jobs_lock:
            _bg_jobs[task_id]['status'] = 'processing'

        import cv2
        import numpy as np
        import urllib.request
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core import base_options as bo

        model_path = os.path.join(_DATA_DIR, 'selfie_segmenter.tflite')
        if not os.path.exists(model_path):
            urllib.request.urlretrieve(
                'https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite',
                model_path
            )

        fg_cap = cv2.VideoCapture(fg_path)
        if not fg_cap.isOpened():
            raise RuntimeError(f"Cannot open video: {fg_path}")

        total_frames = int(fg_cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        width        = int(fg_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height       = int(fg_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps          = fg_cap.get(cv2.CAP_PROP_FPS) or 25.0

        bg_img = cv2.imread(studio_path)
        if bg_img is None:
            raise FileNotFoundError(f"Studio image not found: {studio_path}")
        bg_img = cv2.resize(bg_img, (width, height))
        bg_f32 = bg_img.astype(np.float32) / 255.0

        raw_path = output_path.replace('.mp4', '_raw.mp4')
        writer   = cv2.VideoWriter(raw_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (width, height))
        if not writer.isOpened():
            raise RuntimeError("VideoWriter could not be opened")

        options = vision.ImageSegmenterOptions(
            base_options=bo.BaseOptions(model_asset_path=model_path),
            output_category_mask=True
        )
        segmenter = vision.ImageSegmenter.create_from_options(options)

        frame_idx = 0
        while True:
            ret, frame = fg_cap.read()
            if not ret:
                break
            rgb       = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image  = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result    = segmenter.segment(mp_image)
            mask      = result.category_mask.numpy_view().astype(np.float32)
            mask      = (mask < 0.5).astype(np.float32)
            mask      = cv2.GaussianBlur(mask, (21, 21), 0)
            mask_3ch  = np.stack([mask] * 3, axis=-1)
            fg_f32    = frame.astype(np.float32) / 255.0
            blended   = fg_f32 * mask_3ch + bg_f32 * (1.0 - mask_3ch)
            writer.write(np.clip(blended * 255.0, 0, 255).astype(np.uint8))
            frame_idx += 1
            with _bg_jobs_lock:
                _bg_jobs[task_id]['progress'] = min(88, int(frame_idx / total_frames * 88))

        fg_cap.release()
        writer.release()
        segmenter.close()

        with _bg_jobs_lock:
            _bg_jobs[task_id]['progress'] = 90

        ffmpeg_check = subprocess.run(['ffmpeg', '-version'], capture_output=True)
        if ffmpeg_check.returncode != 0:
            import shutil
            shutil.move(raw_path, output_path)
        else:
            probe = subprocess.run(
                ['ffprobe', '-v', 'error', '-select_streams', 'a',
                 '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', fg_path],
                capture_output=True, timeout=30
            )
            has_audio = probe.returncode == 0 and b'audio' in probe.stdout

            if has_audio:
                cmd = [
                    'ffmpeg', '-y',
                    '-i', raw_path,
                    '-i', fg_path,
                    '-map', '0:v:0',
                    '-map', '1:a:0',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                    '-c:a', 'aac', '-b:a', '128k',
                    '-movflags', '+faststart',
                    output_path
                ]
            else:
                cmd = [
                    'ffmpeg', '-y',
                    '-i', raw_path,
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                    '-movflags', '+faststart',
                    output_path
                ]

            ffmpeg_result = subprocess.run(cmd, capture_output=True, timeout=1200)

            if os.path.exists(raw_path):
                os.remove(raw_path)

            if ffmpeg_result.returncode != 0:
                err_msg = ffmpeg_result.stderr.decode(errors='replace')
                raise RuntimeError(f"ffmpeg failed: {err_msg}")

        with _bg_jobs_lock:
            _bg_jobs[task_id]['progress'] = 95

        # Upload finished video to Cloudinary
        cloudinary_url = cloudinary_upload_path(output_path, resource_type='video', folder='pressjobs/bg_results')

        # Clean up local output file
        try:
            os.remove(output_path)
        except Exception:
            pass

        if not cloudinary_url:
            raise RuntimeError("فشل رفع الفيديو المعالج إلى Cloudinary")

        with _bg_jobs_lock:
            _bg_jobs[task_id]['status']   = 'done'
            _bg_jobs[task_id]['progress'] = 100
            _bg_jobs[task_id]['result']   = cloudinary_url  # full Cloudinary URL

    except Exception as e:
        for p in [output_path, output_path.replace('.mp4', '_raw.mp4')]:
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        with _bg_jobs_lock:
            _bg_jobs[task_id]['status'] = 'error'
            _bg_jobs[task_id]['error']  = str(e)
    finally:
        with _bg_jobs_lock:
            studio_tmp = _bg_jobs.get(task_id, {}).get('studio_tmp')
        if studio_tmp:
            p = os.path.join(UPLOAD_FOLDER, studio_tmp)
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        # Clean up fg temp file
        if os.path.exists(fg_path):
            try: os.remove(fg_path)
            except Exception: pass


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)