from flask import Flask, request, redirect, url_for, render_template, session, flash
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import json
import os
import logging
import time
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, FileField
from wtforms.validators import DataRequired
from flask_wtf.file import FileAllowed
from flask_wtf.csrf import CSRFProtect

app = Flask(__name__)
app.config['SECRET_KEY'] = 'Твой_Сложный_Секретный_Ключ'
app.config['UPLOAD_FOLDER'] = 'static/avatars'
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
csrf = CSRFProtect(app)

# Пути к файлам
USERS_FILE = 'users.json'
HOLIDAYS_FILE = 'holidays.json'
DEFAULT_AVATAR = 'static/images/default_avatar.png'

# Настройка логирования
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


# Загрузка данных
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            users = json.load(f)
            # Добавляем поле favorites если его нет
            for user in users.values():
                if 'favorites' not in user:
                    user['favorites'] = []
            return users
    except FileNotFoundError:
        logging.warning(f"Файл {USERS_FILE} не найден. Возвращаю пустой словарь.")
        return {}
    except json.JSONDecodeError:
        logging.error(f"Ошибка декодирования JSON в файле {USERS_FILE}. Возвращаю пустой словарь.")
        return {}


def save_users(users):
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=4)
        logging.info(f"Данные пользователей успешно сохранены в {USERS_FILE}")
    except Exception as e:
        logging.error(f"Ошибка при сохранении данных пользователей в {USERS_FILE}: {e}")


def load_holidays():
    """Загружает праздники из JSON-файла с проверкой ошибок"""
    if not os.path.exists(HOLIDAYS_FILE):
        # Создаем новый файл с примером данных
        default_holidays = [
            {
                "name": "Новый год",
                "date": "01.01.2025",
                "image": "",
                "text": ""
            }
        ]
        with open(HOLIDAYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_holidays, f, ensure_ascii=False, indent=4)
        return default_holidays

    try:
        with open(HOLIDAYS_FILE, 'r', encoding='utf-8') as f:
            # Проверяем, что файл не пустой
            if os.stat(HOLIDAYS_FILE).st_size == 0:
                raise ValueError("Файл пуст")
            return json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Ошибка загрузки holidays.json: {e}. Создаю новый файл.")
        if os.path.exists(HOLIDAYS_FILE):
            os.rename(HOLIDAYS_FILE, f"holidays_broken_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        # Возвращаем минимальный набор праздников
        return [
            {
                "name": "Пример праздника",
                "date": "01.01.2025",
                "image": "",
                "text": ""
            }
        ]


@app.route('/add_favorite/<holiday_name>')
def add_favorite(holiday_name):
    if 'user' not in session:
        return redirect(url_for('login'))

    users = load_users()
    username = session['user']

    if username in users:
        # Проверяем, что праздник еще не в избранном
        holidays = load_holidays()
        holiday = next((h for h in holidays if h['name'] == holiday_name), None)

        if holiday and holiday_name not in users[username]['favorites']:
            users[username]['favorites'].append(holiday_name)
            save_users(users)
            flash(f'"{holiday_name}" добавлен в избранное', 'success')

    return redirect(request.referrer or url_for('index'))


@app.route('/remove_favorite/<holiday_name>')
def remove_favorite(holiday_name):
    if 'user' not in session:
        return redirect(url_for('login'))

    users = load_users()
    username = session['user']

    if username in users and holiday_name in users[username]['favorites']:
        users[username]['favorites'].remove(holiday_name)
        save_users(users)
        flash(f'"{holiday_name}" удален из избранного', 'info')

    return redirect(request.referrer or url_for('index'))

# Поиск праздников
def find_holiday_by_search(query, holidays):
    if not query or not holidays:
        return None

    query = query.strip().lower()

    # Поиск по точной дате (формат dd.mm.yyyy)
    try:
        search_date = datetime.strptime(query, '%d.%m.%Y').date()
        for holiday in holidays:
            try:
                holiday_date = datetime.strptime(holiday.get("date", ""), '%d.%m.%Y').date()
                if holiday_date == search_date:
                    return holiday
            except (ValueError, AttributeError, KeyError):
                continue
    except ValueError:
        pass

    # Поиск по частичному совпадению даты (например, "01.01")
    try:
        if len(query) == 5 and query[2] == '.' and query.count('.') == 1:  # формат dd.mm
            day, month = query.split('.')
            for holiday in holidays:
                try:
                    holiday_date = holiday.get("date", "")
                    if holiday_date.endswith(f"{day}.{month}"):  # ищем совпадение дня и месяца
                        return holiday
                except (ValueError, AttributeError, KeyError):
                    continue
    except ValueError:
        pass

    # Поиск по названию (частичное совпадение)
    for holiday in holidays:
        if query in holiday.get("name", "").lower():
            return holiday

    # Поиск по тексту (если это файл)
    for holiday in holidays:
        text = holiday.get("text", "")
        if text.endswith('.txt'):
            try:
                with open(text, 'r', encoding='utf-8') as f:
                    content = f.read().lower()
                    if query in content:
                        return holiday
            except Exception:
                continue
        elif query in text.lower():
            return holiday

    return None


# Маршруты
@app.route('/')
def index():
    try:
        # Загружаем праздники
        holidays = load_holidays()
        current_date = datetime.now()
        current_date_str = current_date.strftime('%d.%m.%Y')

        # Инициализация переменных для праздников
        past_holiday = None
        upcoming_holiday = None
        distant_holiday = None
        recipe_text = None

        # Загружаем данные пользователя если он авторизован
        user_data = {}
        if 'user' in session:
            users = load_users()
            user_data = users.get(session['user'], {})
            # Обновляем аватар в сессии если он есть в базе
            if 'avatar' in user_data:
                session['avatar'] = user_data['avatar']

        # Обрабатываем праздники
        if holidays:
            for holiday in holidays:
                try:
                    holiday_date = datetime.strptime(holiday["date"], '%d.%m.%Y') + timedelta(days=1)

                    if holiday_date < current_date:
                        past_holiday = holiday
                    elif current_date <= holiday_date <= current_date + timedelta(days=7):
                        upcoming_holiday = holiday
                    elif holiday_date > current_date + timedelta(days=7):
                        distant_holiday = holiday
                        break
                except (KeyError, ValueError) as e:
                    print(f"Ошибка обработки праздника: {e}")

            # Загружаем рецепт если есть ближайший праздник
            if upcoming_holiday and upcoming_holiday.get("text"):
                try:
                    with open(upcoming_holiday["text"], 'r', encoding='utf-8') as f:
                        recipe_text = f.read()
                except Exception as e:
                    recipe_text = f"Ошибка загрузки рецепта: {str(e)}"
        else:
            print("Предупреждение: список праздников пуст")

        # Рендерим шаблон с всеми данными
        return render_template('store.html',
                               current_date=current_date_str,
                               past_holiday=past_holiday,
                               upcoming_holiday=upcoming_holiday,
                               distant_holiday=distant_holiday,
                               recipe_text=recipe_text,
                               user_data=user_data)  # Передаем данные пользователя

    except Exception as e:
        print(f"Критическая ошибка в index(): {str(e)}")
        # В случае ошибки все равно возвращаем шаблон с минимальными данными
        return render_template('store.html',
                               current_date=datetime.now().strftime('%d.%m.%Y'),
                               past_holiday=None,
                               upcoming_holiday=None,
                               distant_holiday=None,
                               recipe_text="Сервис временно недоступен",
                               user_data={})


def read_recipe(filename):
    """Читает рецепт из файла и возвращает строку, обрабатывая ошибки."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logging.error(f"Ошибка чтения рецепта из {filename}: {e}")
        return f"Ошибка загрузки рецепта: {str(e)}"


@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('query', '')
    holidays = load_holidays()

    if not query:
        return redirect(url_for('index'))

    found_holiday = find_holiday_by_search(query, holidays)

    if found_holiday is None:
        return render_template('not_found.html',
                               query=query,
                               current_date=datetime.now().strftime('%d.%m.%Y'))

    found_holiday_date = None
    if found_holiday.get("date"):
        try:
            found_holiday_date = datetime.strptime(found_holiday["date"], '%d.%m.%Y')
        except ValueError:
            logging.warning(f"Неверный формат даты: {found_holiday['date']}")

    recipe_text = "Рецепт не найден"
    if found_holiday.get("text"):
        recipe_text = read_recipe(found_holiday["text"])

    # Получаем данные пользователя если он авторизован
    user_data = {}
    if 'user' in session:
        users = load_users()
        user_data = users.get(session['user'], {})

    return render_template('found.html',
                           found_holiday=found_holiday,
                           found_holiday_date=found_holiday_date,
                           recipe_text=recipe_text,
                           query=query,
                           current_date=datetime.now().strftime('%d.%m.%Y'),
                           user_data=user_data)


class RegistrationForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    submit = SubmitField('Зарегистрироваться')


@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegistrationForm()

    # Если пользователь уже авторизован - перенаправляем на главную
    if 'user' in session:
        flash('Вы уже авторизованы', 'info')
        return redirect(url_for('index'))

    if form.validate_on_submit():
        try:
            users = load_users()
            username = form.username.data.strip()
            password = form.password.data

            # Валидация имени пользователя
            if len(username) < 4:
                flash('Имя пользователя должно содержать минимум 4 символа', 'error')
                return redirect(url_for('register'))

            if len(password) < 6:
                flash('Пароль должен содержать минимум 6 символов', 'error')
                return redirect(url_for('register'))

            # Проверяем, что пользователь не существует
            if username in users:
                flash('Пользователь с таким именем уже существует', 'error')
                return redirect(url_for('register'))

            # Создаем нового пользователя
            users[username] = {
                'password_hash': generate_password_hash(password),
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'avatar': ''
            }
            save_users(users)

            # Автоматически авторизуем пользователя
            session['user'] = username
            session['avatar'] = ''
            flash('Регистрация прошла успешно! Добро пожаловать!', 'success')
            return redirect(url_for('index'))

        except Exception as e:
            flash(f'Произошла ошибка при регистрации: {str(e)}', 'danger')
            app.logger.error(f'Registration error: {str(e)}')
            return redirect(url_for('register'))

    # Если GET запрос или форма не валидна
    return render_template('register.html',
                           form=form,
                           current_date=datetime.now().strftime('%d.%m.%Y'))


class LoginForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    submit = SubmitField('Войти')


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()

    # Если пользователь уже авторизован - перенаправляем на главную
    if 'user' in session:
        flash('Вы уже авторизованы', 'info')
        return redirect(url_for('index'))

    if form.validate_on_submit():
        try:
            users = load_users()
            username = form.username.data.strip()
            password = form.password.data

            # Проверяем существование пользователя
            if username not in users:
                flash('Пользователь с таким именем не найден', 'error')
                return redirect(url_for('login'))

            # Проверяем пароль
            if not check_password_hash(users[username]['password_hash'], password):
                flash('Неверный пароль', 'error')
                return redirect(url_for('login'))

            # Успешная авторизация
            session['user'] = username
            session['avatar'] = users[username].get('avatar', '')
            flash('Вы успешно вошли в систему!', 'success')
            return redirect(url_for('index'))

        except Exception as e:
            flash(f'Произошла ошибка при авторизации: {str(e)}', 'danger')
            app.logger.error(f'Login error: {str(e)}')
            return redirect(url_for('login'))

    # Добавлен возврат шаблона для GET-запросов
    return render_template('login.html',
                           form=form,
                           current_date=datetime.now().strftime('%d.%m.%Y'))


def allowed_file(filename):
    """Проверяет, что файл имеет разрешенное расширение"""
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions


class UploadAvatarForm(FlaskForm):
    avatar = FileField('Выберите аватар')
    submit = SubmitField('Загрузить')


@app.route('/upload_avatar', methods=['POST'])
def upload_avatar():
    if 'user' not in session:
        flash('Для загрузки аватара необходимо войти в систему', 'error')
        return redirect(url_for('login'))

    form = UploadAvatarForm()

    if form.validate_on_submit():
        file = form.avatar.data
        if not file or file.filename == '':
            flash('Файл не выбран', 'error')
            return redirect(url_for('profile'))

        try:
            # Проверка расширения
            if not allowed_file(file.filename):
                flash('Недопустимый формат файла. Разрешены: png, jpg, jpeg, gif, webp', 'error')
                return redirect(url_for('profile'))

            # Генерация безопасного имени файла
            filename = secure_filename(file.filename)
            # Уникальное имя: avatar_username_timestamp.ext
            ext = filename.rsplit('.', 1)[1].lower()
            filename = f"avatar_{session['user']}_{int(time.time())}.{ext}"
            
            # Сохранение файла
            upload_folder = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'])
            os.makedirs(upload_folder, exist_ok=True)
            
            # Полный путь для сохранения
            save_path = os.path.join(upload_folder, filename)
            file.save(save_path)
            
            # Относительный путь для БД (с forward slashes)
            relative_path = os.path.join('avatars', filename).replace('\\', '/')
            
            # Обновление данных пользователя
            users = load_users()
            username = session['user']
            
            # Удаление старого аватара (если не дефолтный)
            old_avatar = users[username].get('avatar', '')
            if old_avatar and old_avatar != DEFAULT_AVATAR:
                old_path = os.path.join(app.root_path, old_avatar)
                if os.path.exists(old_path):
                    os.remove(old_path)
            
            # Сохранение нового пути
            users[username]['avatar'] = f"static/{relative_path}"
            save_users(users)
            session['avatar'] = users[username]['avatar']
            
            flash('Аватар успешно обновлен!', 'success')
            return redirect(url_for('profile'))
            
        except Exception as e:
            flash(f'Ошибка при загрузке: {str(e)}', 'error')
            app.logger.error(f'Avatar upload error: {str(e)}')
            return redirect(url_for('profile'))
    
    return redirect(url_for('profile'))


@app.route('/profile')
def profile():
    if 'user' not in session:
        # Если пользователь не залогинен, все равно создаем форму
        form = UploadAvatarForm()
        return render_template('profile.html',
                               username=None,
                               avatar=None,
                               user_data={},
                               favorite_holidays=[],
                               current_date=datetime.now().strftime('%d.%m.%Y'),
                               form=form)

    users = load_users()
    username = session['user']
    user_data = users.get(username, {})
    holidays = load_holidays()

    form = UploadAvatarForm()

    # Получаем полную информацию об избранных праздниках
    favorite_holidays = []
    for holiday_name in user_data.get('favorites', []):
        holiday = next((h for h in holidays if h['name'] == holiday_name), None)
        if holiday:
            favorite_holidays.append(holiday)

    # Логируем путь к аватару
    logging.info(f"Путь к аватару пользователя {username}: {user_data.get('avatar', 'Не установлен')}")

    return render_template('profile.html',
                           username=username,
                           avatar=user_data.get('avatar', ''),
                           user_data=user_data,
                           favorite_holidays=favorite_holidays,
                           current_date=datetime.now().strftime('%d.%m.%Y'),
                           form=form)


@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))


if __name__ == '__main__':
    # Создаем папку для аватаров, если ее нет
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    # Создаем файлы если их нет
    if not os.path.exists(USERS_FILE):
        save_users({})
    if not os.path.exists(HOLIDAYS_FILE):
        with open(HOLIDAYS_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)

    app.run(debug=True)
