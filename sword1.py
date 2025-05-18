import os
import time
import requests
from PIL import Image as PILImage, UnidentifiedImageError
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from bs4 import BeautifulSoup
import img2pdf # Using img2pdf
import telegram
from telegram.ext import Application as PTBApplication
from selenium_stealth import stealth
import logging
import re
import shutil
from urllib.parse import urljoin # Keep this
import asyncio
import sys # Added for sys.exit and sys.stdout

# === CONFIGURATION (WITH RENDER MODIFICATIONS) ===
# Try to get from Environment variables (for Render), otherwise use your hardcoded defaults (for local)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '7158770945:AAGurp3ei8OE17bNb77CSYxjO0JpQcRTBDI')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID', '@manhwa_inquisition')
MANGA_MAIN_URL = os.environ.get('MANGA_MAIN_URL', 'https://aquareader.net/manga/heavenly-inquisition-sword/')

# --- PATH CONFIGURATION (WITH RENDER MODIFICATIONS) ---
DOWNLOAD_FOLDER_BASE_NAME = 'downloads' # Your original base name
IS_ON_RENDER_ENV = os.environ.get('RENDER') == 'true'

if IS_ON_RENDER_ENV:
    PERSISTENT_DISK_MOUNT_PATH = '/data'
    DOWNLOAD_FOLDER = os.path.join(PERSISTENT_DISK_MOUNT_PATH, DOWNLOAD_FOLDER_BASE_NAME)
    # Use print for very early logs as logger might not be fully configured yet
    print(f"INFO: Detected RENDER environment. Persistent data target: {DOWNLOAD_FOLDER}")
else:
    DOWNLOAD_FOLDER = DOWNLOAD_FOLDER_BASE_NAME # Your original relative path
    print(f"INFO: Detected LOCAL environment. Data target: ./{DOWNLOAD_FOLDER}")

# All other paths are based on the DOWNLOAD_FOLDER determined above
SCREENSHOT_FOLDER = os.path.join(DOWNLOAD_FOLDER, 'screenshots')
PROBLEM_IMG_PILLOW_FOLDER = os.path.join(DOWNLOAD_FOLDER, 'problem_images_pillow')
PROBLEM_IMG_PDF_FOLDER = os.path.join(DOWNLOAD_FOLDER, 'problem_images_pdf_creation')
DOWNLOADED_CHAPTERS_FILE = os.path.join(DOWNLOAD_FOLDER, 'downloaded_chapters.txt')

# --- Original constants below (no change unless they need to be env vars too) ---
CHECK_INTERVAL_SECONDS = 3600
SELENIUM_WAIT_TIMEOUT = 60
SELENIUM_PAGE_LOAD_TIMEOUT = 120
IMAGE_DOWNLOAD_TIMEOUT = 75
IMAGE_DOWNLOAD_RETRIES = 3
CLEANUP_IMAGES_AFTER_UPLOAD = True
MAX_CHAPTER_DOWNLOAD_ATTEMPTS = 1

# === LOGGING SETUP (Modified for Render Path) ===
LOG_FILE_NAME = 'manga_downloader.log'
LOG_FILE_PATH = os.path.join(DOWNLOAD_FOLDER, LOG_FILE_NAME) # Log will be in the correct DOWNLOAD_FOLDER

# Initial basicConfig - FileHandler will be added in run_main
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)]) # Changed to sys.stdout for Render
logger = logging.getLogger(__name__) # Corrected: use __name__

# === TELEGRAM BOT SETUP ===
application: PTBApplication = None
bot: telegram.Bot = None

# === NEW FUNCTION FOR RENDER ===
def ensure_directories_exist():
    paths_to_create = [
        DOWNLOAD_FOLDER,
        SCREENSHOT_FOLDER,
        PROBLEM_IMG_PILLOW_FOLDER,
        PROBLEM_IMG_PDF_FOLDER,
    ]
    logger.info(f"Ensuring directory structure based at: {DOWNLOAD_FOLDER}")
    for path in paths_to_create:
        if path:
            try:
                os.makedirs(path, exist_ok=True)
                logger.info(f"Directory ensured/created: {path}")
            except OSError as e:
                logger.error(f"Could not create directory {path}: {e}", exc_info=True)
                if path == DOWNLOAD_FOLDER:
                    logger.critical(f"CRITICAL: Cannot create base download folder {DOWNLOAD_FOLDER}. Exiting.")
                    sys.exit(1)

# === SELENIUM SETUP (WITH RENDER MODIFICATIONS) ===
def setup_driver():
    logger.info("Setting up Selenium WebDriver...")
    # ensure_directories_exist() called in run_main will create SCREENSHOT_FOLDER
    # os.makedirs(SCREENSHOT_FOLDER, exist_ok=True) # Not strictly needed here if called earlier

    opts = ChromeOptions()

    if IS_ON_RENDER_ENV: # This global flag must be defined above
        logger.info("RENDER Environment: Configuring Chrome for headless operation.")
        opts.add_argument('--headless=new')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--disable-gpu')
    else:
        logger.info("LOCAL Environment: Configuring Chrome for visible operation (headless commented).")
        # YOUR ORIGINAL: # opts.add_argument('--headless=new') # Comment out to see browser
        # No changes here needed if you want it visible locally. If you had other local-specific
        # options that are different from Render, they would go here.
        pass # No specific headless options for local by default now

    # Common options from your script
    opts.add_argument('--disable-gpu'); # This is fine for both, but redundant if already set in IS_ON_RENDER_ENV
    if not IS_ON_RENDER_ENV: # Avoid duplicate --no-sandbox if already added for Render
         opts.add_argument('--no-sandbox') # Your original had this always
    if not IS_ON_RENDER_ENV: # Avoid duplicate --disable-dev-shm-usage
         opts.add_argument('--disable-dev-shm-usage');
    opts.add_argument('--log-level=3')
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
    opts.add_argument("--blink-settings=imagesEnabled=true"); opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_experimental_option("excludeSwitches", ["enable-automation"]); opts.add_experimental_option('useAutomationExtension', False)
    opts.add_argument('--start-maximized'); opts.add_argument('--window-size=1920,1080'); opts.add_argument('--accept-lang=en-US,en;q=0.9,ko-KR;q=0.8,ko;q=0.7')
    opts.add_argument("--disable-features=WebRtcHideLocalIpsWithMdns,LazyImageLoading,MediaEngagementBypassAutoplayPolicies")
    opts.add_argument("--disable-infobars"); opts.add_argument("--disable-popup-blocking"); opts.add_argument("--disable-notifications"); opts.add_argument("--disable-logging")
    opts.add_argument('--ignore-certificate-errors'); opts.add_argument("--allow-running-insecure-content")
    opts.add_argument('--disable-extensions');
    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        logger.info("Applying selenium-stealth patches...")
        # Adjust platform for stealth based on environment
        stealth_platform = "Linux x86_64" if IS_ON_RENDER_ENV else "Win32"
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform=stealth_platform, webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", run_on_insecure_origins=False)
        logger.info("Selenium-stealth patches applied.")
        driver.set_page_load_timeout(SELENIUM_PAGE_LOAD_TIMEOUT)
        logger.info("Selenium WebDriver setup successful with stealth.")
        return driver
    except Exception as e:
        logger.error(f"Failed to setup Selenium WebDriver: {e}", exc_info=True); raise

# === REQUESTS SESSION (Your original) ===
image_session = requests.Session()
image_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Referer': MANGA_MAIN_URL, 'Accept-Language': 'en-US,en;q=0.9', 'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8' # Corrected your original typo image/,/*
})

# === UTILITY FUNCTIONS (Your original - check sanitize_filename carefully) ===
IMAGE_MIME_TYPES_EXTENSIONS = { 'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp', 'image/bmp': '.bmp' }
def get_extension_from_content_type(content_type_header):
    if not content_type_header: return None
    content_type = content_type_header.split(';')[0].strip().lower()
    return IMAGE_MIME_TYPES_EXTENSIONS.get(content_type)

def sanitize_filename(name): # YOUR ORIGINAL SANITIZE LOGIC
    name = str(name)
    # name = re.sub(r'[\\/*?:"<>|]', "", name) # Your original had \ escaped, fixed
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.replace(':', '-').replace('/', '-').replace(' ', '_') # Changed your replace ' ' to '_'
    # name = re.sub(r'_+', '_', name) # Your original had r'+', fixed to r'_+'
    name = re.sub(r'_+', '_', name)
    name = name.strip('_.- ') # Your original had '.- ', changed to '_.- '
    return name if name else "untitled"

def robust_scroll_to_bottom(driver_instance, scroll_pause_time=1.5, max_scroll_checks=4, max_total_scrolls=35):
    logger.debug("Robust scrolling to bottom...")
    last_height = driver_instance.execute_script("return document.body.scrollHeight")
    no_change_checks = 0; scroll_attempts = 0;
    while scroll_attempts < max_total_scrolls :
        driver_instance.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause_time)
        if scroll_attempts % 4 == 0 and scroll_attempts > 0:
            logger.debug("Nudging scroll to trigger lazy loads...")
            driver_instance.execute_script("window.scrollBy(0, -Math.floor(window.innerHeight/4));")
            time.sleep(0.7)
            driver_instance.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.7)
        new_height = driver_instance.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            no_change_checks += 1
            if no_change_checks >= max_scroll_checks: logger.debug(f"Scroll height no change {max_scroll_checks}x. Assuming bottom."); break
        else: no_change_checks = 0
        last_height = new_height; scroll_attempts += 1
        logger.debug(f"Scroll attempt {scroll_attempts}/{max_total_scrolls}, height: {new_height}")
    if scroll_attempts >= max_total_scrolls: logger.warning("Max total scroll attempts reached.")
    logger.debug("Finished robust scrolling attempt.")

def convert_image_for_pdf(image_path, quality=85): # YOUR ORIGINAL CONVERT LOGIC
    base_name = os.path.basename(image_path)
    logger.debug(f"CONVERT_PDF: Processing for PDF compatibility: {image_path}")
    if not os.path.exists(image_path) or os.path.getsize(image_path) < 100:
        logger.warning(f"CONVERT_PDF: Image {image_path} not found or too small. Skipping.")
        return None
    try:
        with PILImage.open(image_path) as img:
            img.load()
            original_format = img.format
            # original_mode was used in a condition I removed as per your request to not change functions
            # If your original logic depended on it, it might need to be re-added if this breaks
            # original_mode = img.mode
            logger.info(f"CONVERT_PDF: Opened {base_name}: Format={original_format}, Mode={img.mode}, Size={img.size}")
            if img.width == 0 or img.height == 0:
                logger.warning(f"CONVERT_PDF: {base_name} has zero dimensions. Skipping.")
                return None
            if img.mode == 'P':
                logger.info(f"CONVERT_PDF: {base_name} is mode 'P'. Converting to RGBA then RGB.")
                img = img.convert('RGBA').convert('RGB')
            elif img.mode == 'RGBA' or img.mode == 'LA':
                logger.info(f"CONVERT_PDF: {base_name} mode {img.mode} has alpha. Blending with white, converting to RGB.")
                background = PILImage.new("RGB", img.size, (255, 255, 255))
                alpha_mask = img.split()[-1] if img.mode in ['RGBA', 'LA'] else None
                background.paste(img, mask=alpha_mask)
                img = background
            elif img.mode not in ('RGB', 'L'):
                logger.info(f"CONVERT_PDF: {base_name} mode {img.mode}. Converting to RGB.")
                img = img.convert('RGB')
            output_path = image_path
            # Simplified condition based on your original logic for WEBP or non-JPG/PNG
            if original_format == 'WEBP' or (original_format not in ['JPEG', 'PNG']):
                new_filename_base = os.path.splitext(base_name)[0]
                output_path = os.path.join(os.path.dirname(image_path), new_filename_base + ".jpg")
                logger.info(f"CONVERT_PDF: Saving {base_name} as JPG: {output_path}")
                img.save(output_path, "JPEG", quality=quality, optimize=True, progressive=True)
                if image_path.lower() != output_path.lower() and os.path.exists(image_path):
                    try: os.remove(image_path); logger.debug(f"CONVERT_PDF: Removed original {image_path}")
                    except OSError as e: logger.warning(f"CONVERT_PDF: Could not remove original {image_path}: {e}")
            logger.debug(f"CONVERT_PDF: Final path for {base_name} is {output_path}")
            return output_path
    except UnidentifiedImageError: logger.error(f"CONVERT_PDF: Pillow UnidentifiedImageError: {image_path}")
    except FileNotFoundError: logger.error(f"CONVERT_PDF: Pillow FileNotFoundError: {image_path}")
    except Exception as e: logger.error(f"CONVERT_PDF: Pillow error processing {image_path}: {e}", exc_info=True)
    try:
        os.makedirs(PROBLEM_IMG_PILLOW_FOLDER, exist_ok=True) # Path is now conditional
        if os.path.exists(image_path): shutil.copy(image_path, os.path.join(PROBLEM_IMG_PILLOW_FOLDER, "pillow_problem_" + os.path.basename(image_path))); logger.info(f"CONVERT_PDF: Copied problematic {os.path.basename(image_path)} to {PROBLEM_IMG_PILLOW_FOLDER}.")
    except Exception as copy_e: logger.warning(f"CONVERT_PDF: Could not copy problematic image: {copy_e}")
    return None

def load_downloaded_chapters():
    downloaded = set()
    if os.path.exists(DOWNLOADED_CHAPTERS_FILE): # Path is now conditional
        try:
            with open(DOWNLOADED_CHAPTERS_FILE, 'r', encoding='utf-8') as f: downloaded = set(line.strip() for line in f if line.strip())
            logger.info(f"Loaded {len(downloaded)} downloaded chapters.")
        except Exception as e: logger.error(f"Error loading downloaded_chapters.txt: {e}")
    return downloaded

def save_downloaded_chapter(chapter_title):
    try:
        with open(DOWNLOADED_CHAPTERS_FILE, 'a', encoding='utf-8') as f: f.write(chapter_title + '\n') # Path is now conditional
        logger.debug(f"Saved '{chapter_title}' to downloaded list.")
    except Exception as e: logger.error(f"Error saving downloaded chapter '{chapter_title}': {e}")

def handle_overlays(driver_instance, attempt=1): # YOUR ORIGINAL
    logger.debug(f"Checking for overlays (attempt {attempt})...")
    overlay_selectors = [ "button[id*='consent']", "button[class*='consent']", "div[class*='cookie-banner'] button", "div[class*='cookie-notice'] button", "button[aria-label*='Accept']", "button[aria-label*='Dismiss']", "button[aria-label*='Close']", "span[aria-label*='Close']", "i[class*='close']", "div[id*='poperblock'] button.close", "div.fc-dialog button.fc-cta-consent", "div#gdpr-consent-tool-wrapper button[data-gdpr-action='accept']", "button#ez-accept-all", "div[aria-modal='true'] button[aria-label='Close']" ]
    clicked_overlay = False
    for selector in overlay_selectors:
        try:
            elements = driver_instance.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed() and element.is_enabled():
                    logger.info(f"Attempting to click overlay: {selector}")
                    try: driver_instance.execute_script("arguments[0].click();", element); logger.info(f"Clicked overlay (JS): {selector}"); time.sleep(2.5); clicked_overlay = True; break
                    except Exception as e_js:
                        logger.warning(f"JS click fail for {selector}: {e_js}. Trying direct.")
                        try: element.click(); logger.info(f"Clicked overlay (direct): {selector}"); time.sleep(2.5); clicked_overlay = True; break
                        except Exception as e_click: logger.error(f"Direct click also fail for {selector}: {e_click}")
            if clicked_overlay: break
        except NoSuchElementException: pass
        except Exception as e_find: logger.warning(f"Error finding/interacting with overlay {selector}: {e_find}")
    logger.debug("Finished checking for overlays.")
    return clicked_overlay

# === CORE LOGIC FUNCTIONS (Your original) ===
def get_all_chapters(driver_instance):
    logger.info(f"Fetching chapters from {MANGA_MAIN_URL}...")
    screenshot_name_base = sanitize_filename(MANGA_MAIN_URL) # Path to SCREENSHOT_FOLDER is now conditional
    try:
        driver_instance.get(MANGA_MAIN_URL); time.sleep(3); handle_overlays(driver_instance, 1); time.sleep(2); handle_overlays(driver_instance, 2)
    except TimeoutException as pte: logger.error(f"Page load timeout for {MANGA_MAIN_URL}: {pte}"); s_path = os.path.join(SCREENSHOT_FOLDER, f"err_pgload_{screenshot_name_base}.png"); driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}"); return []
    except Exception as e: logger.error(f"Error loading {MANGA_MAIN_URL}: {e}"); s_path = os.path.join(SCREENSHOT_FOLDER, f"err_load_generic_{screenshot_name_base}.png"); driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}"); return []
    chapters = []
    try:
        logger.info("Waiting for BODY tag..."); WebDriverWait(driver_instance, SELENIUM_WAIT_TIMEOUT).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        logger.info("BODY present. Scroll & wait 15s for JS..."); robust_scroll_to_bottom(driver_instance, 1.5, 2, 10); time.sleep(15); handle_overlays(driver_instance, 3)
        page_source = driver_instance.page_source; soup = BeautifulSoup(page_source, 'html.parser')
        chapter_container_selectors = [ 'div.page-content-listing.single-page ul.main.version-chap ul.sub-chap-list', 'div.page-content-listing.single-page ul.main.version-chap', 'ul.main.version-chap', 'div.eplister ul', 'div.clstyle ul', 'div#chapterlist ul', '.postbody .entry-content ul' ]
        chapter_list_container = None
        for sel in chapter_container_selectors:
            chapter_list_container = soup.select_one(sel)
            if chapter_list_container: logger.info(f"Found chapter list container: '{sel}'"); break
        if not chapter_list_container: logger.error(f"No known chapter list container on {MANGA_MAIN_URL}."); s_path = os.path.join(SCREENSHOT_FOLDER, f"err_no_chap_container_{screenshot_name_base}.png"); driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}"); return []
        chapter_link_elements = chapter_list_container.select('li.wp-manga-chapter > a[href]')
        if not chapter_link_elements: chapter_link_elements = chapter_list_container.select('li > a[href]')
        if not chapter_link_elements: logger.error(f"No links in container: {chapter_list_container.name if chapter_list_container else 'None'}"); s_path = os.path.join(SCREENSHOT_FOLDER, f"err_nochaplinks_in_container_{screenshot_name_base}.png"); driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}"); return []
        processed_urls = set()
        for tag in chapter_link_elements:
            url, title = tag.get('href'), tag.text.strip()
            if url and title and 1 < len(title) < 150:
                full_url = urljoin(MANGA_MAIN_URL, url)
                if MANGA_MAIN_URL in full_url and full_url not in processed_urls:
                    if "chapter" in title.lower() or re.search(r'\d', title) or "ch." in title.lower(): chapters.append({'title': title, 'url': full_url}); processed_urls.add(full_url)
                    else: logger.debug(f"Skip link, title not like chapter: '{title}' ({full_url})")
        if chapters: chapters.reverse(); logger.info(f"Found {len(chapters)} unique chapter entries.")
        else: logger.warning("No valid chapter entries extracted."); s_path = os.path.join(SCREENSHOT_FOLDER, f"err_novalidchaps_extracted_{screenshot_name_base}.png"); driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}")
    except TimeoutException: logger.error(f"Timeout waiting for BODY tag on {MANGA_MAIN_URL}."); s_path = os.path.join(SCREENSHOT_FOLDER, f"err_timeout_body_{screenshot_name_base}.png"); driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}")
    except Exception as e: logger.error(f"Error fetching/parsing chapters: {e}", exc_info=True); s_path = os.path.join(SCREENSHOT_FOLDER, f"err_parsechaps_{screenshot_name_base}.png"); driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}")
    return chapters

def download_images_for_chapter(driver_instance, chapter_title, chapter_url): # YOUR ORIGINAL
    safe_title = sanitize_filename(chapter_title)
    logger.info(f"Downloading images for: {chapter_title} ({chapter_url})")
    try:
        driver_instance.get(chapter_url); time.sleep(1); handle_overlays(driver_instance,1); time.sleep(0.5); handle_overlays(driver_instance,2)
    except Exception as e:
        logger.error(f"Error loading chapter page {chapter_url}: {e}")
        s_path = os.path.join(SCREENSHOT_FOLDER, f"err_load_chap_pg_{safe_title}.png") # Path conditional
        try: driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}")
        except: pass
        return []
    try:
        WebDriverWait(driver_instance, SELENIUM_WAIT_TIMEOUT).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.reading-content")))
        logger.info("Container 'div.reading-content' present.")
    except TimeoutException:
        logger.warning(f"Timeout for 'div.reading-content' on {chapter_url}. Proceeding.")
        s_path = os.path.join(SCREENSHOT_FOLDER, f"warn_noimgcontainer_{safe_title}.png") # Path conditional
        try: driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}")
        except: pass

    robust_scroll_to_bottom(driver_instance, 2.0, 4, 35); time.sleep(3)
    soup = BeautifulSoup(driver_instance.page_source, 'html.parser')
    reading_content_div = soup.select_one('div.reading-content')
    if not reading_content_div:
        logger.warning(f"'div.reading-content' not found. Fallback selectors for {chapter_title}.")
        for sel in ['#readerarea', '.entry-content .text-left', '.viewer_img_container', '.comic-reader__container']:
            reading_content_div = soup.select_one(sel)
            if reading_content_div: logger.info(f"Found reading area via fallback: '{sel}'"); break
    if not reading_content_div:
        logger.error(f"No main reading area for {chapter_title}. SS."); s_path = os.path.join(SCREENSHOT_FOLDER, f"err_noreadarea_{safe_title}.png") # Path conditional
        try: driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}")
        except: pass
        return []

    img_tags = reading_content_div.select('div.page-break > img.wp-manga-chapter-img')
    if not img_tags: img_tags = reading_content_div.find_all('img', class_='wp-manga-chapter-img')
    if not img_tags: img_tags = reading_content_div.find_all('img')

    logger.info(f"Found {len(img_tags)} image elements in reading area for {chapter_title}.")
    if not img_tags:
        logger.error(f"No <img> tags in reading area for {chapter_title}. SS."); s_path = os.path.join(SCREENSHOT_FOLDER, f"err_noimagesinreadarea_{safe_title}.png") # Path conditional
        try: driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}")
        except: pass
        return []

    chapter_folder = os.path.join(DOWNLOAD_FOLDER, safe_title); os.makedirs(chapter_folder, exist_ok=True) # Path conditional
    image_files_for_pdf = []

    for idx, img_element in enumerate(img_tags):
        img_url = None; attrs_to_check = ['src', 'data-src', 'data-lazy-src', 'data-lazyload', 'data-original', 'data-pagespeed-lazy-src']
        for attr in attrs_to_check:
            candidate_url = img_element.get(attr)
            if candidate_url and candidate_url.strip() and not candidate_url.lower().startswith('data:image') and len(candidate_url.strip()) > 10:
                img_url = candidate_url.strip(); break
        if not img_url and img_element.get('srcset'):
            try: img_url = [s.strip().split(' ')[0] for s in img_element.get('srcset').strip().split(',') if s.strip()][0]
            except: pass

        if not img_url or img_url.lower().startswith('data:image') or 'base64,' in img_url.lower() or len(img_url) < 10:
            logger.debug(f"Skip invalid/placeholder img src ({img_url}) page {idx+1}"); continue

        img_url = urljoin(chapter_url, img_url)
        filename_base = f'page_{idx + 1:04d}'
        dl_success = False; downloaded_content_filepath = None

        potential_processed_jpg = os.path.join(chapter_folder, f"{filename_base}.jpg")
        if os.path.exists(potential_processed_jpg) and os.path.getsize(potential_processed_jpg) > 1024:
            logger.debug(f"Page {idx+1} ({potential_processed_jpg}) already exists as processed JPG. Using existing.")
            image_files_for_pdf.append(potential_processed_jpg)
            continue

        for attempt in range(IMAGE_DOWNLOAD_RETRIES):
            try:
                logger.debug(f"DL Attempt {attempt+1} for Page {idx+1} URL: {img_url}")
                r = image_session.get(img_url, timeout=IMAGE_DOWNLOAD_TIMEOUT, stream=True)
                r.raise_for_status()
                ct = r.headers.get('Content-Type', '').lower()
                guessed_ext = get_extension_from_content_type(ct)
                if not guessed_ext:
                    logger.warning(f"Page {idx+1} ({img_url}) NOT img. CT: '{ct}'. Skip.")
                    dl_success = False; break
                downloaded_content_filepath = os.path.join(chapter_folder, f"{filename_base}{guessed_ext}")
                with open(downloaded_content_filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=262144): f.write(chunk)
                if os.path.exists(downloaded_content_filepath) and os.path.getsize(downloaded_content_filepath) > 100:
                    logger.debug(f"DL Page {idx+1} to {downloaded_content_filepath} (Att {attempt+1}).")
                    dl_success = True; break
                else:
                    logger.warning(f"DL Page {idx+1} empty/small. Retry {attempt+1}/{IMAGE_DOWNLOAD_RETRIES}.")
                    if os.path.exists(downloaded_content_filepath): os.remove(downloaded_content_filepath)
            except requests.exceptions.HTTPError as http_err:
                logger.warning(f"HTTP err {http_err.response.status_code} Page {idx+1} (Att {attempt+1}): {img_url}")
                if http_err.response.status_code in [404, 403, 401]: dl_success = False; break
                if attempt == IMAGE_DOWNLOAD_RETRIES - 1: logger.error(f"Failed DL Page {idx+1} after {IMAGE_DOWNLOAD_RETRIES} HTTPError attempts.")
                else: time.sleep(3 * (attempt + 1))
            except requests.exceptions.RequestException as e_req:
                logger.warning(f"DL attempt {attempt+1} Page {idx+1} RequestException fail: {e_req}")
                if attempt == IMAGE_DOWNLOAD_RETRIES - 1: logger.error(f"Failed DL Page {idx+1} after {IMAGE_DOWNLOAD_RETRIES} RequestException attempts.")
                else: time.sleep(3 * (attempt + 1))

        if dl_success and downloaded_content_filepath:
            processed_path = convert_image_for_pdf(downloaded_content_filepath)
            if processed_path and os.path.exists(processed_path):
                image_files_for_pdf.append(processed_path)
            else:
                logger.warning(f"Img proc/convert fail for {downloaded_content_filepath} or result missing.")
        elif not dl_success:
             logger.error(f"Skipping Page {idx+1} for {chapter_title} due to persistent download/type issue for URL: {img_url}")

    logger.info(f"Successfully processed {len(image_files_for_pdf)} images for PDF for {chapter_title}.")
    if not image_files_for_pdf and len(img_tags) > 0:
        logger.error(f"CRITICAL: No images for PDF for {chapter_title}, though {len(img_tags)} elements found.");
        s_path = os.path.join(SCREENSHOT_FOLDER, f"err_noimgfilesprocessed_{safe_title}.png") # Path conditional
        try: driver_instance.save_screenshot(s_path); logger.info(f"SS: {s_path}")
        except: pass
    return image_files_for_pdf

def images_to_pdf(image_files, pdf_path): # YOUR ORIGINAL
    if not image_files:
        logger.warning("PDF img2pdf: No image files provided. PDF not created.")
        return False
    image_files.sort()
    logger.info(f"PDF img2pdf: Attempting to convert {len(image_files)} images to PDF: {pdf_path}")
    logger.debug(f"PDF img2pdf: Sorted image file list: {image_files}")
    valid_image_paths_for_img2pdf = []
    skipped_images = 0
    for img_idx, img_path in enumerate(image_files):
        logger.debug(f"PDF img2pdf: Validating image {img_idx + 1}/{len(image_files)}: {img_path}")
        if not os.path.exists(img_path) or os.path.getsize(img_path) < 100:
            logger.warning(f"PDF img2pdf: Skipping (not found or too small): {img_path}"); skipped_images += 1; continue
        try:
            with PILImage.open(img_path) as pil_img:
                pil_img.load();
                if pil_img.width == 0 or pil_img.height == 0: logger.warning(f"PDF img2pdf: Skipping zero dim image: {img_path}"); skipped_images += 1; continue
            valid_image_paths_for_img2pdf.append(img_path)
            logger.debug(f"PDF img2pdf: Added {os.path.basename(img_path)} to list for PDF.")
        except UnidentifiedImageError: logger.error(f"PDF img2pdf: Pillow cannot identify image: {img_path}. SKIPPING."); skipped_images += 1
        except Exception as e_pil: logger.error(f"PDF img2pdf: Pillow error validating {img_path}: {e_pil}. SKIPPING."); skipped_images += 1
    if not valid_image_paths_for_img2pdf: logger.error(f"PDF img2pdf: No valid images left for PDF {pdf_path}."); return False
    try:
        pdf_bytes = img2pdf.convert(valid_image_paths_for_img2pdf)
        with open(pdf_path, "wb") as f: f.write(pdf_bytes) # Path conditional (via pdf_path)
        final_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        logger.info(f"PDF img2pdf: Successfully saved PDF: {pdf_path} ({final_size_mb:.2f}MB), {len(valid_image_paths_for_img2pdf)} pages.")
        if skipped_images > 0: logger.warning(f"PDF img2pdf: {skipped_images} images SKIPPED before PDF creation.")
        return True
    except img2pdf.PdfTooBigError: logger.error(f"PDF img2pdf: PdfTooBigError for {pdf_path}.")
    except Exception as e_img2pdf: logger.error(f"PDF img2pdf: Error during PDF creation with img2pdf for {pdf_path}: {e_img2pdf}", exc_info=True)
    return False

async def upload_pdf_to_telegram_async(pdf_path, caption_text): # YOUR ORIGINAL
    global bot
    if not bot: logger.warning("Bot not init for async upload. Skip upload."); return False
    if not os.path.exists(pdf_path): logger.error(f"PDF not found for async upload: {pdf_path}"); return False
    logger.info(f"Async Uploading {os.path.basename(pdf_path)} to {TELEGRAM_CHANNEL_ID} (caption: '{caption_text[:50]}...')") # TELEGRAM_CHANNEL_ID from env
    try:
        file_size_mb = os.path.getsize(pdf_path) / (1024*1024)
        if file_size_mb > 49.8: logger.warning(f"PDF {pdf_path} large ({file_size_mb:.2f}MB). May fail if >50MB.")
        if len(caption_text) > 1020: caption_text = caption_text[:1020] + "..."
        with open(pdf_path, 'rb') as pdf_file:
            await bot.send_document(chat_id=TELEGRAM_CHANNEL_ID, document=pdf_file, caption=caption_text, filename=os.path.basename(pdf_path))
        logger.info(f"Async: Uploaded {os.path.basename(pdf_path)} to Telegram.")
        return True
    except telegram.error.NetworkError as e: logger.error(f"TG NetError (async) {pdf_path}: {e}.")
    except telegram.error.BadRequest as e: logger.error(f"TG BadRequest (async) {pdf_path}: {e}")
    except telegram.error.TimedOut: logger.error(f"TG TimedOut (async) {pdf_path}.")
    except telegram.error.TelegramError as e: logger.error(f"TG API Error (async) {pdf_path}: {e}")
    except Exception as e: logger.error(f"Unexpected async upload error {pdf_path}: {e}", exc_info=True)
    return False

async def main_loop_async(driver_instance): # YOUR ORIGINAL (paths inside will be conditional)
    # os.makedirs(DOWNLOAD_FOLDER, exist_ok=True) # Moved to ensure_directories_exist
    downloaded_titles = load_downloaded_chapters()
    while True:
        logger.info("=== Starting New Chapter Check Cycle (Async Loop) ===")
        try:
            all_chaps = get_all_chapters(driver_instance)
            if not all_chaps: logger.warning("No chapters found on site this cycle.")
            new_chaps = [ch for ch in all_chaps if ch['title'] not in downloaded_titles]
            if not new_chaps: logger.info(f"No new chaps. On site: {len(all_chaps)}. Downloaded: {len(downloaded_titles)}.")
            else:
                logger.info(f"Found {len(new_chaps)} new chapters.")
                for ch_data in new_chaps:
                    title, url = ch_data['title'], ch_data['url']
                    safe_t = sanitize_filename(title); img_fld = os.path.join(DOWNLOAD_FOLDER, safe_t)
                    pdf_n = f"{safe_t}.pdf"; pdf_p = os.path.join(DOWNLOAD_FOLDER, pdf_n)
                    logger.info(f"--- Processing Chapter: {title} ({url}) ---")
                    processed_ok = False
                    for attempt in range(MAX_CHAPTER_DOWNLOAD_ATTEMPTS):
                        logger.info(f"Attempt {attempt+1}/{MAX_CHAPTER_DOWNLOAD_ATTEMPTS} for '{title}'")
                        imgs = download_images_for_chapter(driver_instance, title, url)
                        if not imgs:
                            logger.warning(f"No images for '{title}' attempt {attempt+1}.")
                            if attempt < MAX_CHAPTER_DOWNLOAD_ATTEMPTS-1:
                                logger.info("Waiting 30s, then refreshing page before retrying..."); await asyncio.sleep(30)
                                try: driver_instance.refresh(); logger.info("Refreshed page.")
                                except Exception as r_e: logger.warning(f"Refresh failed: {r_e}")
                                continue
                            else: break
                        pdf_ok = images_to_pdf(imgs, pdf_p)
                        if not pdf_ok:
                            logger.error(f"PDF fail for '{title}' attempt {attempt+1}.")
                            if attempt < MAX_CHAPTER_DOWNLOAD_ATTEMPTS-1: await asyncio.sleep(10); continue
                            else: break
                        upload_ok = await upload_pdf_to_telegram_async(pdf_p, title)
                        if upload_ok:
                            downloaded_titles.add(title); save_downloaded_chapter(title)
                            logger.info(f"SUCCESS: '{title}' processed & uploaded.")
                            processed_ok = True
                            try:
                                if os.path.exists(pdf_p): os.remove(pdf_p); logger.debug(f"Cleaned PDF: {pdf_p}")
                            except OSError as e: logger.warning(f"Could not rm PDF {pdf_p}: {e}")
                            break
                        else:
                            logger.error(f"Upload fail for '{title}' attempt {attempt+1}.")
                            if attempt < MAX_CHAPTER_DOWNLOAD_ATTEMPTS-1: logger.info("Waiting 60s before retrying upload..."); await asyncio.sleep(60); continue
                            else: break
                    if CLEANUP_IMAGES_AFTER_UPLOAD and processed_ok:
                        if os.path.isdir(img_fld):
                            try: shutil.rmtree(img_fld); logger.info(f"Cleaned images: {img_fld}")
                            except OSError as e: logger.warning(f"Could not rm img folder {img_fld}: {e}")
                    elif not processed_ok and os.path.isdir(img_fld): logger.info(f"Images {img_fld} kept (processing fail).")
                    if not processed_ok: logger.error(f"Failed to process '{title}' after {MAX_CHAPTER_DOWNLOAD_ATTEMPTS} attempts.")
                    logger.info(f"--- Finished for chapter: {title} ---"); await asyncio.sleep(20) # Your original sleep
        except requests.exceptions.ConnectionError as e: logger.error(f"Connection error main loop: {e}. Retrying {CHECK_INTERVAL_SECONDS/2}s."); await asyncio.sleep(CHECK_INTERVAL_SECONDS/2)
        except asyncio.CancelledError:
            logger.info("Main loop was cancelled. Propagating cancellation for shutdown.")
            raise
        except Exception as e: logger.critical(f"Critical main_loop error: {e}", exc_info=True); await asyncio.sleep(CHECK_INTERVAL_SECONDS/2)
        logger.info(f"=== Cycle Complete. Next check in {CHECK_INTERVAL_SECONDS/3600:.1f}h ==="); await asyncio.sleep(CHECK_INTERVAL_SECONDS)

async def run_main(): # MODIFIED FOR RENDER
    global application, bot

    ensure_directories_exist() # Call this first!

    # Add FileHandler for logging now that directories exist
    try:
        file_log_handler = logging.FileHandler(LOG_FILE_PATH, mode='a', encoding='utf-8') # Path is now conditional
        file_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'))
        logging.getLogger().addHandler(file_log_handler) # Add to the root logger
        logger.info(f"File logging enabled to: {LOG_FILE_PATH}")
    except Exception as e_log_file:
        logger.error(f"Failed to setup file logging to {LOG_FILE_PATH}: {e_log_file}")


    if IS_ON_RENDER_ENV and (TELEGRAM_BOT_TOKEN == '7158770945:AAGurp3ei8OE17bNb77CSYxjO0JpQcRTBDI' or not TELEGRAM_BOT_TOKEN): # Check if default token is used on Render
        logger.warning("WARNING: On Render, but TELEGRAM_BOT_TOKEN is default or missing. Check Render Env Vars.")
    if not TELEGRAM_BOT_TOKEN: # This uses the value from os.environ.get or fallback
        logger.critical("TG Token not set after checking env and fallback. Exiting."); sys.exit(1) # Changed from warning to critical
    if not TELEGRAM_CHANNEL_ID:
        logger.critical("TG Channel ID not set. Exiting."); sys.exit(1)


    logger.info("Initializing Telegram Application (PTB v20+ style)...")
    try: # Your original try-except for bot init
        application_builder = PTBApplication.builder().token(TELEGRAM_BOT_TOKEN)
        application_builder.connect_timeout(20.0); application_builder.read_timeout(180.0)
        application_builder.write_timeout(240.0); application_builder.pool_timeout(15.0)
        application = application_builder.build(); bot = application.bot
        logger.info("Telegram App and Bot initialized successfully.")
    except ImportError: logger.critical("Import PTBApplication fail. PTB v20+ not installed/corrupt?"); sys.exit(1) # Changed to critical
    except Exception as e: logger.critical(f"Error during TG Bot/App init: {e}"); sys.exit(1) # Changed to critical

    # if not bot: logger.critical("TG Bot not initialized. Exiting."); return # This was redundant

    driver = None
    logger.info(f"Script starting... Mode: {'Render' if IS_ON_RENDER_ENV else 'Local'}. Data Dir: {os.path.abspath(DOWNLOAD_FOLDER)}")
    try:
        driver = setup_driver()
        await main_loop_async(driver)
    except KeyboardInterrupt:
        logger.info("Script interrupted by user (KeyboardInterrupt). Shutting down...")
    except asyncio.CancelledError:
        logger.info("Script execution was cancelled. Shutting down...")
    except Exception as e:
        logger.critical(f"Fatal top-level exception: {e}", exc_info=True)
    finally:
        if driver:
            logger.info("Quitting Selenium WebDriver...")
            try: driver.quit()
            except Exception as e_q: logger.error(f"Error quitting WebDriver: {e_q}")
        logger.info("Script finished.")

if __name__ == '__main__': # MODIFIED FOR RENDER
    # This early check for Render environment and default/missing tokens is crucial
    if os.environ.get('RENDER') == 'true' and \
       (os.environ.get('TELEGRAM_BOT_TOKEN') == '7158770945:AAGurp3ei8OE17bNb77CSYxjO0JpQcRTBDI' or \
        os.environ.get('TELEGRAM_BOT_TOKEN') is None or \
        os.environ.get('TELEGRAM_CHANNEL_ID') == '@manhwa_inquisition' or \
        os.environ.get('TELEGRAM_CHANNEL_ID') is None):
        print("CRITICAL: Running on RENDER environment but TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID "
              "are using hardcoded defaults or are not set. Please ensure they are set as Environment Variables in your Render service settings.", file=sys.stderr)
        sys.exit(1) # Exit if on Render and critical secrets are not overridden via ENV

    # Your original os.makedirs calls here are now handled by ensure_directories_exist() in run_main()
    # os.makedirs(PROBLEM_IMG_PILLOW_FOLDER, exist_ok=True) # Path is now conditional
    # os.makedirs(PROBLEM_IMG_PDF_FOLDER, exist_ok=True)    # Path is now conditional

    try:
        asyncio.run(run_main())
    except KeyboardInterrupt:
        # This catches Ctrl+C if it happens directly on asyncio.run() before the main try/except in run_main is entered
        print("Script interrupted by user at the very top level (asyncio.run). Exiting.")
    except Exception as e_top_run:
        # Fallback for any other totally unhandled exception during asyncio.run()
        print(f"CRITICAL UNHANDLED EXCEPTION at asyncio.run() level: {e_top_run}", file=sys.stderr)