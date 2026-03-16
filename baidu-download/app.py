#!/usr/bin/env python3
"""
网盘下载器 - 搜索 + 转存下载
"""
from flask import Flask, request, jsonify, render_template, send_from_directory
import base64
import json
import os
import re
import threading
import time
import urllib.parse
import uuid
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

import requests
from playwright.sync_api import sync_playwright

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

SEARCH_API = os.environ.get('SEARCH_API', 'http://localhost:8081/api/search')
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_PATH = Path(os.environ.get('DOWNLOAD_PATH', '/mnt/shipin'))
BDUSS = os.environ.get('BDUSS', 'xzeGpFfkRhcy0tMmp0UGZ4NHJpQUtabUc2TWRCUUFKVVZYV1FPWjdYV0xoalZwRVFBQUFBJCQAAAAAAQAAAAEAAABkl1GIx-W357bJw~fUwnRpbWUAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIv5DWmL-Q1pY2')
ALIST_BASE = os.environ.get('ALIST_BASE', 'http://localhost:8083').rstrip('/')
ALIST_USERNAME = os.environ.get('ALIST_USERNAME', 'admin')
ALIST_PASSWORD = os.environ.get('ALIST_PASSWORD', 'cxy123456')
try:
    ALIST_SHARE_STORAGE_ID = int(os.environ.get('ALIST_SHARE_STORAGE_ID', '3'))
except (TypeError, ValueError):
    ALIST_SHARE_STORAGE_ID = 0
ALIST_SHARE_MOUNT = os.environ.get('ALIST_SHARE_MOUNT', '/tmp-share')
ALIST_NETDISK_MOUNT = os.environ.get('ALIST_NETDISK_MOUNT', '/baidu')
ALIST_QUARK_MOUNT = os.environ.get('ALIST_QUARK_MOUNT', '/夸克网盘')
_raw_quark_folder = os.environ.get('QUARK_TARGET_FOLDER_PATH', '影视下载').strip()
if _raw_quark_folder in ('', '/'):
    QUARK_TARGET_FOLDER_PATH = '影视下载'
else:
    QUARK_TARGET_FOLDER_PATH = _raw_quark_folder.strip('/')
ALIST_TIMEOUT = int(os.environ.get('ALIST_TIMEOUT', '30'))
ALIST_DIRECT_DOWNLOAD = os.environ.get('ALIST_DIRECT_DOWNLOAD', '1').strip().lower() in ('1', 'true', 'yes', 'y')

ALIYUN_API_BASE = 'https://api.aliyundrive.com'
ALIYUN_TIMEOUT = int(os.environ.get('ALIYUN_TIMEOUT', '30'))

GLOBAL_HTTP_PROXY = os.environ.get('GLOBAL_HTTP_PROXY')
if GLOBAL_HTTP_PROXY:
    REQUESTS_PROXIES = {
        'http': GLOBAL_HTTP_PROXY,
        'https': GLOBAL_HTTP_PROXY
    }
else:
    REQUESTS_PROXIES = None

PLAYWRIGHT_PROXY = os.environ.get('PLAYWRIGHT_PROXY') or GLOBAL_HTTP_PROXY

CAPTURE_DIR = Path(os.environ.get('CAPTURE_DIR', str(BASE_DIR / 'captures')))

CAPTCHA_TOKEN_FILE = Path(os.environ.get('CAPTCHA_TOKEN_FILE', str(BASE_DIR / 'captcha_token.txt')))
CAPTCHA_TYPE_FILE = Path(os.environ.get('CAPTCHA_TYPE_FILE', str(BASE_DIR / 'captcha_type.txt')))

CLOUD_CAPTCHA_TOKEN = os.environ.get('CLOUD_CAPTCHA_TOKEN', '').strip()
if not CLOUD_CAPTCHA_TOKEN and CAPTCHA_TOKEN_FILE.exists():
    CLOUD_CAPTCHA_TOKEN = CAPTCHA_TOKEN_FILE.read_text().strip()

CLOUD_CAPTCHA_TYPE = os.environ.get('CLOUD_CAPTCHA_TYPE', '').strip() or '10103'
if (not CLOUD_CAPTCHA_TYPE or CLOUD_CAPTCHA_TYPE == '10103') and CAPTCHA_TYPE_FILE.exists():
    value = CAPTCHA_TYPE_FILE.read_text().strip()
    if value:
        CLOUD_CAPTCHA_TYPE = value
CAPTCHA_API_URL = os.environ.get('CAPTCHA_API_URL', 'http://api.jfbym.com/api/YmServer/customApi').strip()
CAPTCHA_TIMEOUT = int(os.environ.get('CAPTCHA_TIMEOUT', '30'))

QUARK_API_BASE = os.environ.get('QUARK_API_BASE', 'https://drive-h.quark.cn').rstrip('/')
QUARK_COMMON_QUERY = '?pr=ucpro&fr=pc&uc_param_str='
QUARK_APP_VERSION = os.environ.get('QUARK_APP_VERSION', '4.5.90')
QUARK_CLIENT_TYPE = os.environ.get('QUARK_CLIENT_TYPE', 'web')
QUARK_COOKIE = os.environ.get('QUARK_COOKIE', '').strip()
QUARK_TIMEOUT = int(os.environ.get('QUARK_TIMEOUT', '30'))
QUARK_TARGET_FOLDER_FID = os.environ.get('QUARK_TARGET_FOLDER_FID', '').strip()
QUARK_DOWNLOAD_BASE = os.environ.get('QUARK_DOWNLOAD_BASE', 'https://drive-pc.quark.cn').rstrip('/')

DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

download_tasks = {}
task_lock = threading.Lock()
playwright_lock = threading.Lock()
alist_client = None
alist_client_lock = threading.Lock()


class NeedVerificationError(RuntimeError):
    pass


class AlistClient:
    def __init__(self, base, username, password, timeout=30, share_storage_id=0):
        self.base = base.rstrip('/')
        self.username = username
        self.password = password
        self.timeout = timeout
        self.share_storage_id = share_storage_id
        self._token = None
        self._token_expire = 0
        self._auth_lock = threading.Lock()

    def _login(self):
        payload = {'username': self.username, 'password': self.password}
        resp = requests.post(f"{self.base}/api/auth/login", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get('code') != 200:
            raise RuntimeError(data.get('message') or 'Alist 登录失败')
        token = data.get('data', {}).get('token')
        if not token:
            raise RuntimeError('Alist 登录未返回 token')
        self._token = token
        self._token_expire = time.time() + 2 * 60 * 60

    def _get_token(self):
        with self._auth_lock:
            if self._token and time.time() < self._token_expire - 60:
                return self._token
            self._login()
            return self._token

    def _request(self, method, path, **kwargs):
        token = self._get_token()
        headers = kwargs.setdefault('headers', {})
        headers['Authorization'] = token
        url = f"{self.base}{path}"
        resp = requests.request(method, url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError as exc:
            raise RuntimeError('Alist 响应解析失败') from exc
        if body.get('code') != 200:
            raise RuntimeError(body.get('message') or 'Alist 接口异常')
        return body.get('data')

    def get_storage(self, storage_id):
        return self._request('GET', f"/api/admin/storage/get?id={storage_id}")

    def mount_share(self, surl, password, sekey=None):
        if not self.share_storage_id:
            raise RuntimeError('未配置 Alist 分享存储 ID')
        storage = self.get_storage(self.share_storage_id) or {}
        addition_raw = storage.get('addition') or '{}'
        try:
            addition = json.loads(addition_raw)
        except ValueError:
            addition = {}
        addition.update({
            'root_folder_path': '/',
            'surl': surl,
            'pwd': password or '',
            'BDUSS': BDUSS
        })
        if sekey:
            addition['sekey'] = sekey
        payload = {
            'id': storage.get('id') or self.share_storage_id,
            'mount_path': storage.get('mount_path', ALIST_SHARE_MOUNT),
            'driver': storage.get('driver', 'BaiduShare'),
            'order': storage.get('order', 0),
            'cache_expiration': storage.get('cache_expiration', 30),
            'addition': json.dumps(addition, ensure_ascii=False)
        }
        self._request('POST', '/api/admin/storage/update', json=payload)

    def list_dir(self, path, refresh=False):
        payload = {'path': path}
        if refresh:
            payload['refresh'] = True
        data = self._request('POST', '/api/fs/list', json=payload) or {}
        return data.get('content') or []

    def get_download_info(self, path):
        payload = {'path': path}
        data = self._request('POST', '/api/fs/get', json=payload) or {}
        return data

    @staticmethod
    def _join_path(base_path, name):
        base = base_path.rstrip('/')
        if not base:
            return f"/{name}"
        return f"{base}/{name}"

    def collect_files(self, mount_path, refresh=False):
        files = []
        queue = deque([(mount_path, '', refresh)])
        seen = set()
        while queue:
            current_path, relative_path, need_refresh = queue.popleft()
            if (current_path, relative_path) in seen:
                continue
            seen.add((current_path, relative_path))
            items = self.list_dir(current_path, refresh=need_refresh)
            for item in items:
                name = item.get('name')
                if not name:
                    continue
                next_path = self._join_path(current_path, name)
                next_relative = f"{relative_path}/{name}" if relative_path else name
                if str(item.get('is_dir')) == '1' or item.get('is_dir') is True:
                    queue.append((next_path, next_relative, False))
                else:
                    files.append({
                        'path': next_path,
                        'relative': next_relative,
                        'name': name,
                        'size': int(item.get('size') or 0)
                    })
        return files


def get_alist_client():
    if not (ALIST_BASE and ALIST_USERNAME and ALIST_PASSWORD):
        return None
    global alist_client
    with alist_client_lock:
        if alist_client is None:
            alist_client = AlistClient(
                ALIST_BASE,
                ALIST_USERNAME,
                ALIST_PASSWORD,
                timeout=ALIST_TIMEOUT,
                share_storage_id=ALIST_SHARE_STORAGE_ID,
            )
        return alist_client


def upsert_task(task_id, **fields):
    with task_lock:
        task = download_tasks.setdefault(task_id, {
            'task_id': task_id,
            'status': 'pending',
            'progress': 0,
            'filename': '',
            'message': '',
            'provider': 'unknown',
            'captcha': '',
            'created_at': time.time(),
            'downloaded_bytes': 0,
            'total_bytes': 0,
            'speed_bps': 0.0,
        })
        task.update(fields)
        return task.copy()


def get_task(task_id):
    with task_lock:
        return download_tasks.get(task_id, {}).copy()


def list_tasks():
    with task_lock:
        return sorted(download_tasks.values(), key=lambda x: x['created_at'], reverse=True)


def human_size(num):
    step = 1024.0
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    for unit in units:
        if num < step:
            return f"{num:.1f} {unit}"
        num /= step
    return f"{num:.1f} EB"


def extract_surl(share_url):
    match = re.search(r'/s/([A-Za-z0-9_-]+)', share_url or '')
    if not match:
        return None
    return normalize_surl(match.group(1))


def normalize_surl(surl):
    if surl and surl.startswith('1') and len(surl) > 1:
        return surl[1:]
    return surl


def sanitize_segment(name):
    if not name:
        return 'file'
    cleaned = re.sub(r'[\\/:*?"<>|]+', '_', name.strip())
    return cleaned or 'file'


def build_local_path(base_dir, relative_name, fallback):
    base_dir.mkdir(parents=True, exist_ok=True)
    rel = PurePosixPath(relative_name or '')
    parts = []
    for part in rel.parts:
        if part in ('.', '..'):
            continue
        parts.append(sanitize_segment(part))
    if not parts:
        parts = [sanitize_segment(fallback)]
    target = base_dir.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def normalize_quark_segment(name):
    if not name:
        return ''
    return re.sub(r'[（(]\d+[)）]$', '', name).strip()


def build_quark_alist_path(relative_name):
    if not ALIST_QUARK_MOUNT:
        raise RuntimeError('未配置 ALIST_QUARK_MOUNT，无法通过 AList 下载夸克文件')
    mount_path = PurePosixPath(ALIST_QUARK_MOUNT)
    if QUARK_TARGET_FOLDER_PATH:
        mount_path = mount_path / PurePosixPath(QUARK_TARGET_FOLDER_PATH)
    rel = PurePosixPath(relative_name or '')
    for part in rel.parts:
        if part in ('', '.', '..'):
            continue
        mount_path = mount_path / part
    return str(mount_path)


def refresh_alist_path_chain(client, relative_name):
    if not client:
        return
    base = PurePosixPath(ALIST_QUARK_MOUNT or '/')
    chain = [base]
    if QUARK_TARGET_FOLDER_PATH:
        base = base / PurePosixPath(QUARK_TARGET_FOLDER_PATH)
        chain.append(base)
    rel = PurePosixPath(relative_name or '')
    for part in rel.parts:
        if part in ('', '.', '..'):
            continue
        base = base / part
        chain.append(base)
    for path in chain:
        try:
            client.list_dir(str(path), refresh=True)
        except Exception as exc:
            print(f"[quark] 刷新 {path} 失败: {exc}")


def try_get_alist_file_info(client, relative_name):
    if not (client and relative_name):
        return None, None
    try:
        alist_path = build_quark_alist_path(relative_name)
    except Exception:
        return None, None
    refresh_alist_path_chain(client, relative_name)
    try:
        info = client.get_download_info(alist_path)
        if info:
            return alist_path, info
    except Exception:
        pass
    alt_path = locate_quark_alist_path_with_hint(client, relative_name)
    if not alt_path:
        return None, None
    try:
        info = client.get_download_info(alt_path)
        if info:
            return alt_path, info
    except Exception:
        pass
    return None, None


def locate_quark_alist_path_with_hint(client, relative_name):
    if not (client and relative_name and ALIST_QUARK_MOUNT):
        return None
    mount_path = PurePosixPath(ALIST_QUARK_MOUNT)
    if QUARK_TARGET_FOLDER_PATH:
        mount_path = mount_path / PurePosixPath(QUARK_TARGET_FOLDER_PATH)
    segments = [part for part in PurePosixPath(relative_name).parts if part not in ('', '.', '..')]
    current = mount_path
    for segment in segments:
        try:
            entries = client.list_dir(str(current), refresh=True)
        except Exception:
            return None
        candidate = None
        for entry in entries:
            if (entry.get('name') or '') == segment:
                candidate = entry
                break
        if candidate is None:
            normalized_target = normalize_quark_segment(segment)
            for entry in entries:
                if normalize_quark_segment(entry.get('name') or '') == normalized_target:
                    candidate = entry
                    break
        if candidate is None:
            return None
        current = PurePosixPath(AlistClient._join_path(str(current), candidate.get('name')))
    return str(current)


def wait_for_alist_file_info(client, alist_path, timeout=600, interval=5, relative_hint=None):
    parent = str(PurePosixPath(alist_path).parent) or '/'
    deadline = time.time() + timeout
    last_error = None
    resolved_path = alist_path
    hint_used = False
    last_chain_refresh = 0.0
    while time.time() < deadline:
        if relative_hint and (time.time() - last_chain_refresh) >= 30:
            try:
                refresh_alist_path_chain(client, relative_hint)
            except Exception as chain_err:
                last_error = chain_err
            finally:
                last_chain_refresh = time.time()
        try:
            client.list_dir(parent, refresh=True)
            info = client.get_download_info(resolved_path)
            if info:
                return info
            if relative_hint and not hint_used:
                alt_path = locate_quark_alist_path_with_hint(client, relative_hint)
                if alt_path and alt_path != resolved_path:
                    print(f"[quark] AList 路径调整: {resolved_path} -> {alt_path}")
                    resolved_path = alt_path
                    parent = str(PurePosixPath(resolved_path).parent) or '/'
                    hint_used = True
                    continue
        except Exception as exc:
            last_error = exc
        time.sleep(interval)
    if last_error:
        raise RuntimeError(f'Alist 在 {timeout}s 内未找到文件：{last_error}')
    raise RuntimeError('Alist 在限定时间内未找到文件，请稍后重试')


def parse_alist_headers(raw_headers):
    if not raw_headers:
        return {}
    if isinstance(raw_headers, dict):
        return raw_headers
    headers = {}
    for item in raw_headers:
        if isinstance(item, dict):
            key = item.get('name') or item.get('key')
            value = item.get('value')
            if key and value is not None:
                headers[key] = value
    return headers


def download_stream_to_file(url, headers, destination, task_id=None, total_bytes=None):
    if not url:
        raise RuntimeError('未获取到可用的下载地址')
    actual_headers = parse_alist_headers(headers)
    timeout = max(ALIST_TIMEOUT, 30)
    request_kwargs = {
        'headers': actual_headers,
        'stream': True,
        'timeout': timeout * 4
    }
    if REQUESTS_PROXIES:
        request_kwargs['proxies'] = REQUESTS_PROXIES
    start_time = time.time()
    last_report = 0.0
    downloaded = 0
    with requests.get(url, **request_kwargs) as resp:
        resp.raise_for_status()
        if not total_bytes:
            try:
                total_bytes = int(resp.headers.get('Content-Length') or 0)
            except Exception:
                total_bytes = None
        if task_id:
            upsert_task(task_id, downloaded_bytes=0, total_bytes=total_bytes or 0, speed_bps=0.0)
        with open(destination, 'wb') as handle:
            for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if task_id:
                        now = time.time()
                        if now - last_report >= 1 or downloaded == total_bytes:
                            elapsed = max(now - start_time, 0.001)
                            speed = downloaded / elapsed
                            upsert_task(task_id, downloaded_bytes=downloaded, total_bytes=total_bytes or 0, speed_bps=speed)
                            last_report = now
        if task_id:
            upsert_task(task_id, downloaded_bytes=downloaded, total_bytes=total_bytes or 0, speed_bps=0.0)
    return downloaded


def fetch_verification_captcha(session):
    try:
        resp = session.get('https://pan.baidu.com/api/getcaptcha?prod=shareverify', timeout=15)
        data = resp.json()
        img_url = data.get('vcode_img')
        vcode_str = data.get('vcode_str')
        if not img_url or not vcode_str:
            print(f"[captcha] invalid captcha response: {data}")
            return None
        img_resp = session.get(img_url, timeout=15)
        img_resp.raise_for_status()
        image_bytes = img_resp.content
        filename = f"captcha_{int(time.time())}_{uuid.uuid4().hex[:6]}.png"
        path = CAPTURE_DIR / filename
        path.write_bytes(image_bytes)
        print(f"[captcha] saved image {filename}, vcode={vcode_str}")
        return {'filename': filename, 'vcode': vcode_str, 'image': image_bytes}
    except Exception as exc:
        print(f'获取验证码图片失败: {exc}')
        return None


def save_verification_snapshot(share_url):
    filename = f"snapshot_{int(time.time())}_{uuid.uuid4().hex[:6]}.png"
    path = CAPTURE_DIR / filename
    try:
        with playwright_lock:
            with sync_playwright() as playwright:
                launch_kwargs = {'headless': True, 'args': ['--no-sandbox']}
                if PLAYWRIGHT_PROXY:
                    launch_kwargs['proxy'] = {'server': PLAYWRIGHT_PROXY}
                browser = playwright.chromium.launch(**launch_kwargs)
                page = browser.new_page()
                page.goto(share_url, wait_until='load', timeout=30000)
                page.wait_for_timeout(2000)
                page.screenshot(path=str(path))
                browser.close()
        return filename
    except Exception as exc:
        print(f'保存验证码截图失败: {exc}')
        return None


def solve_captcha_via_cloud(image_bytes):
    if not (CLOUD_CAPTCHA_TOKEN and image_bytes):
        return None
    try:
        image_b64 = base64.b64encode(image_bytes).decode('utf-8')
        payload = {
            'token': CLOUD_CAPTCHA_TOKEN,
            'type': CLOUD_CAPTCHA_TYPE or '10103',
            'image': image_b64,
        }
        print(f"[captcha] request payload: type={payload['type']}, token_present={bool(CLOUD_CAPTCHA_TOKEN)}, size={len(image_bytes)}")
        resp = requests.post(CAPTCHA_API_URL, json=payload, timeout=CAPTCHA_TIMEOUT, proxies=REQUESTS_PROXIES)
        resp.raise_for_status()
        data = resp.json()
        print(f'[captcha] response: {data}')
        if data.get('code') == 10000:
            answer = (data.get('data') or {}).get('data') or data.get('msg')
            if answer:
                return str(answer).strip()
        else:
            print(f"[captcha] unexpected code: {data.get('code')}, msg={data.get('msg')}")
    except Exception as exc:
        print(f'云码识别失败: {exc}')
    return None


def mark_verification_required(task_id, share_url, session=None, captcha_data=None):
    captcha = captcha_data
    if captcha is None and session is not None:
        captcha = fetch_verification_captcha(session)
    filename = captcha['filename'] if isinstance(captcha, dict) else captcha
    if not filename:
        filename = save_verification_snapshot(share_url)
    message = '百度需要人机验证，请打开链接完成验证或根据截图手动处理后重新提交任务。'
    payload = {'status': 'error', 'progress': 0, 'message': message}
    if filename:
        payload['captcha'] = filename
    upsert_task(task_id, **payload)


def attempt_auto_verification(task_id, share_url, session):
    captcha = fetch_verification_captcha(session)
    if not captcha:
        mark_verification_required(task_id, share_url, session=session)
        return None
    filename = captcha['filename']
    code = solve_captcha_via_cloud(captcha.get('image'))
    if not code:
        mark_verification_required(task_id, share_url, session=session, captcha_data=captcha)
        return None
    upsert_task(
        task_id,
        status='queued',
        progress=0,
        message=f'已自动填写验证码，正在重试... (识别为 {code})',
        captcha=filename
    )
    return {'code': code, 'vcode': captcha.get('vcode'), 'filename': filename}


def extract_aliyun_share_id(share_url):
    if not share_url:
        return None
    patterns = [
        r'(?:aliyundrive|alipan)\.com/s/([A-Za-z0-9]+)',
        r'/s/([A-Za-z0-9]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, share_url)
        if match:
            return match.group(1)
    return None


def build_proxy_kwargs() -> Dict[str, Any]:
    return {'proxies': REQUESTS_PROXIES} if REQUESTS_PROXIES else {}


def build_aliyun_headers(share_token=None):
    headers = {
        'User-Agent': BROWSER_UA,
        'Content-Type': 'application/json'
    }
    if share_token:
        headers['x-share-token'] = share_token
    return headers


def request_aliyun_share_token(share_id, password):
    payload = {'share_id': share_id, 'share_pwd': password or ''}
    resp = requests.post(
        f"{ALIYUN_API_BASE}/v2/share_link/get_share_token",
        json=payload,
        headers=build_aliyun_headers(),
        timeout=ALIYUN_TIMEOUT,
        **build_proxy_kwargs()
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get('share_token')
    if not token:
        raise RuntimeError('阿里云盘未返回 share_token')
    return token


def aliyun_list_children(share_id, share_token, parent_file_id='root'):
    items = []
    marker = ''
    attempts = 0
    while True:
        payload = {
            'share_id': share_id,
            'parent_file_id': parent_file_id,
            'limit': 200,
            'all': False,
            'order_by': 'name',
            'order_direction': 'ASC'
        }
        if marker:
            payload['marker'] = marker
        resp = requests.post(
            f"{ALIYUN_API_BASE}/adrive/v3/file/list",
            json=payload,
            headers=build_aliyun_headers(share_token),
            timeout=ALIYUN_TIMEOUT,
            **build_proxy_kwargs()
        )
        resp.raise_for_status()
        data = resp.json()
        entries = data.get('items') or []
        items.extend(entries)
        marker = data.get('next_marker') or ''
        attempts += 1
        if not marker or attempts >= 50:
            break
    return items


def collect_aliyun_files(share_id, share_token):
    files: List[Dict[str, Any]] = []
    queue = deque([('root', '')])
    visited = set()
    while queue:
        parent_file_id, relative = queue.popleft()
        if parent_file_id in visited:
            continue
        visited.add(parent_file_id)
        children = aliyun_list_children(share_id, share_token, parent_file_id=parent_file_id)
        for child in children:
            name = child.get('name') or '文件'
            next_relative = f"{relative}/{name}" if relative else name
            ctype = (child.get('type') or '').lower()
            if ctype in ('folder', 'dir'):
                queue.append((child.get('file_id'), next_relative))
            elif ctype == 'file':
                files.append({
                    'file_id': child.get('file_id'),
                    'name': name,
                    'relative': next_relative,
                    'size': int(child.get('size') or 0)
                })
        if len(files) >= 200:
            break
    return files


def select_aliyun_file(files):
    if not files:
        return None
    files.sort(key=lambda item: (item.get('size') or 0, item.get('relative') or ''), reverse=True)
    return files[0]


def get_aliyun_download_url(share_id, share_token, file_id):
    payload = {
        'share_id': share_id,
        'file_id': file_id,
        'expire_sec': 14400
    }
    endpoints = [
        f"{ALIYUN_API_BASE}/v2/file/get_download_url",
        f"{ALIYUN_API_BASE}/v2/share_link/get_share_link_download_url"
    ]
    last_error = None
    for endpoint in endpoints:
        try:
            resp = requests.post(
                endpoint,
                json=payload,
                headers=build_aliyun_headers(share_token),
                timeout=ALIYUN_TIMEOUT,
                **build_proxy_kwargs()
            )
            resp.raise_for_status()
            data = resp.json()
            download_url = data.get('download_url') or data.get('url')
            if download_url:
                return data
            last_error = RuntimeError(data.get('message') or '未返回下载地址')
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError('无法获取下载地址')


def download_aliyun_share(share_url, password, task_id):
    try:
        upsert_task(task_id, status='preparing', progress=5, message='解析阿里云盘分享...')
        share_id = extract_aliyun_share_id(share_url)
        if not share_id:
            raise RuntimeError('无法识别阿里云盘分享链接')

        share_token = request_aliyun_share_token(share_id, password)
        upsert_task(task_id, status='preparing', progress=20, message='列出分享文件...')

        files = collect_aliyun_files(share_id, share_token)
        if not files:
            raise RuntimeError('分享中没有可下载的文件')
        target = select_aliyun_file(files)
        if not target:
            raise RuntimeError('未能定位到可下载的文件')
        filename = target.get('name') or '未命名文件'
        upsert_task(task_id, status='downloading', progress=40, filename=filename, message='获取直链中...')

        download_info = get_aliyun_download_url(share_id, share_token, target.get('file_id'))
        download_url = download_info.get('download_url') or download_info.get('url')
        if not download_url:
            raise RuntimeError('阿里云盘未返回下载地址')

        destination = build_local_path(DOWNLOAD_PATH, target.get('relative'), filename)
        download_headers = {
            'User-Agent': BROWSER_UA,
            'Referer': 'https://www.alipan.com/'
        }
        upsert_task(task_id, status='downloading', progress=65, filename=filename, message='下载中...')
        total_bytes = int(target.get('size') or download_info.get('size') or 0)
        download_stream_to_file(download_url, download_headers, destination, task_id=task_id, total_bytes=total_bytes)
        upsert_task(task_id, status='completed', progress=100, filename=destination.name, message='下载完成')
    except Exception as exc:
        upsert_task(task_id, status='error', progress=0, message=str(exc))


def download_quark_share(share_url, password, task_id):
    share_code = extract_quark_share_code(share_url)
    if not share_code:
        upsert_task(task_id, status='error', progress=0, message='无法解析夸克分享链接')
        return
    session = build_quark_session()
    if not QUARK_COOKIE:
        upsert_task(task_id, status='error', progress=0, message='请配置 QUARK_COOKIE（登录夸克后在浏览器复制 Cookie）')
        return
    try:
        try:
            session.get(share_url, timeout=QUARK_TIMEOUT)
        except requests.RequestException:
            pass
        upsert_task(task_id, status='preparing', progress=5, message='获取夸克分享信息...')
        stoken, token_data = quark_share_token(session, share_code, password)
        share_info, files = quark_collect_files(session, share_code, stoken)
        files = sort_quark_files(files)
        if not files:
            raise RuntimeError('分享中没有可下载的文件')
        share_id = share_info.get('share_id')
        if not share_id:
            raise RuntimeError('未获取到夸克 share_id')
        total_files = len(files)
        aggregate_total = sum(max(int(item.get('size') or 0), 0) for item in files)
        aggregate_downloaded = 0
        saved_once = False
        alist_client_instance = None

        def record_progress(delta, fallback_size=0):
            nonlocal aggregate_downloaded, aggregate_total
            value = int(delta or 0)
            if value <= 0 and fallback_size:
                value = int(fallback_size)
            if value <= 0:
                return
            aggregate_downloaded += value
            if aggregate_total == 0:
                aggregate_total = aggregate_downloaded
            elif aggregate_downloaded > aggregate_total:
                aggregate_downloaded = aggregate_total

        for idx, target in enumerate(files, start=1):
            filename = target.get('name') or '未命名文件'
            file_size = int(target.get('size') or 0)
            relative_path = target.get('relative') or filename
            destination = build_local_path(DOWNLOAD_PATH, relative_path, filename)
            base_progress = int(((idx - 1) / total_files) * 100)
            prefix = f'({idx}/{total_files}) '
            upsert_task(
                task_id,
                status='downloading',
                progress=min(base_progress + 5, 95),
                filename=filename,
                message=f'{prefix}获取夸克直链中...'
            )

            payload = {
                'fids': [target.get('fid')],
                'share_id': share_id,
                'stoken': stoken,
                'share_fid_token': target.get('share_fid_token'),
                'scene': 'share',
                'client_type': QUARK_CLIENT_TYPE,
                'app_ver': QUARK_APP_VERSION
            }
            download_url = None
            try:
                data, raw = quark_api_request(session, 'POST', '/1/clouddrive/file/download', json=payload)
                download_url = extract_quark_download_url(raw)
            except requests.HTTPError as direct_exc:
                status_code = direct_exc.response.status_code if direct_exc.response else None
                print(f"[quark] direct download http error: status={status_code}")
            except RuntimeError as direct_err:
                print(f"[quark] direct download runtime error: {direct_err}")

            if download_url:
                headers = {
                    'User-Agent': QUARK_USER_AGENT,
                    'Referer': 'https://pan.quark.cn/'
                }
                upsert_task(
                    task_id,
                    status='downloading',
                    progress=min(base_progress + 40, 99),
                    filename=filename,
                    message=f'{prefix}直链下载中...'
                )
                written = download_stream_to_file(download_url, headers, destination, task_id=task_id, total_bytes=file_size)
                record_progress(written, file_size)
                upsert_task(
                    task_id,
                    status='downloading',
                    progress=int((idx / total_files) * 100),
                    filename=destination.name,
                    message=f'{prefix}直链下载完成',
                    downloaded_bytes=aggregate_downloaded,
                    total_bytes=aggregate_total
                )
                continue

            if alist_client_instance is None:
                alist_client_instance = get_alist_client()
            if not alist_client_instance:
                raise RuntimeError('夸克已转存，但未配置 Alist，无法继续下载')

            relative_for_alist = relative_path
            alist_path, info = try_get_alist_file_info(alist_client_instance, relative_for_alist)
            if info:
                saved_once = True
            else:
                if not saved_once:
                    upsert_task(
                        task_id,
                        status='transferring',
                        progress=min(base_progress + 55, 97),
                        filename=filename,
                        message=f'{prefix}夸克直链受限，正在转存...'
                    )
                    print('[quark] direct download unavailable, start save workflow')
                    task_save_id = quark_save_share(session, share_code, stoken)
                    print(f'[quark] save task id: {task_save_id}')
                    saved_fids = quark_poll_task(session, task_save_id)
                    if not saved_fids:
                        raise RuntimeError('夸克转存成功但未返回文件信息')
                    saved_once = True
                    print(f'[quark] saved top fids: {saved_fids}')
                refresh_alist_path_chain(alist_client_instance, relative_for_alist)
                wait_message = f'{prefix}等待 AList 同步夸克转存文件...'
                upsert_task(
                    task_id,
                    status='transferring',
                    progress=min(base_progress + 70, 98),
                    filename=filename,
                    message=wait_message
                )
                expected_path = alist_path or build_quark_alist_path(relative_for_alist)
                info = wait_for_alist_file_info(
                    alist_client_instance,
                    expected_path,
                    relative_hint=relative_for_alist
                )
                alist_path = expected_path
            download_url = (info or {}).get('raw_url') or (info or {}).get('url')
            if download_url and download_url.startswith('/'):
                download_url = f"{ALIST_BASE}{download_url}"
            if not download_url:
                raise RuntimeError('AList 未返回下载地址，请稍后重试')
            headers = (info or {}).get('headers') or (info or {}).get('header')
            if not headers:
                headers = {
                    'User-Agent': QUARK_USER_AGENT,
                    'Referer': 'https://pan.quark.cn/'
                }
            upsert_task(
                task_id,
                status='downloading',
                progress=min(base_progress + 85, 99),
                filename=filename,
                message=f'{prefix}通过 AList 下载夸克文件...',
                downloaded_bytes=aggregate_downloaded,
                total_bytes=aggregate_total
            )
            written = download_stream_to_file(download_url, headers, destination, task_id=task_id, total_bytes=file_size)
            record_progress(written, file_size)
            upsert_task(
                task_id,
                status='downloading',
                progress=int((idx / total_files) * 100),
                filename=destination.name,
                message=f'{prefix}下载完成（AList）',
                downloaded_bytes=aggregate_downloaded,
                total_bytes=aggregate_total
            )

        upsert_task(
            task_id,
            status='completed',
            progress=100,
            filename=f'共 {total_files} 个文件',
            message=f'全部下载完成，共 {total_files} 个文件',
            downloaded_bytes=aggregate_downloaded,
            total_bytes=aggregate_total,
            speed_bps=0.0
        )
        return
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response else None
        body_text = exc.response.text[:200] if exc.response and exc.response.text else ''
        print(f'[quark] http error status={status_code} body={body_text}')
        if status_code == 401:
            msg = '夸克接口提示未登录，请更新 QUARK_COOKIE'
        elif status_code == 404:
            msg = '夸克暂不支持匿名下载该文件，可能需要保存到自己网盘后再下载'
        else:
            msg = f'夸克接口异常: {exc}'
        upsert_task(task_id, status='error', progress=0, message=msg)
    except Exception as exc:
        upsert_task(task_id, status='error', progress=0, message=str(exc))


def fetch_sign_via_playwright(share_url, password):
    with playwright_lock:
        with sync_playwright() as playwright:
            launch_kwargs = {'headless': True, 'args': ['--no-sandbox']}
            if PLAYWRIGHT_PROXY:
                launch_kwargs['proxy'] = {'server': PLAYWRIGHT_PROXY}
            browser = playwright.chromium.launch(**launch_kwargs)
            try:
                context = browser.new_context(user_agent=BROWSER_UA)
                cookies: list[dict[str, Any]] = [
                    {'name': 'BDUSS', 'value': BDUSS, 'domain': '.baidu.com', 'path': '/'},
                    {'name': 'BDUSS_BFESS', 'value': BDUSS, 'domain': '.baidu.com', 'path': '/'}
                ]
                if STOKEN:
                    cookies.append({'name': 'STOKEN', 'value': STOKEN, 'domain': '.baidu.com', 'path': '/'})
                context.add_cookies(cookies)  # type: ignore[arg-type]
                page = context.new_page()
                page.goto(share_url, wait_until='domcontentloaded')
                if password:
                    try:
                        page.fill('input#accessCode', password)
                        page.keyboard.press('Enter')
                    except Exception:
                        pass
                page.wait_for_timeout(2000)
                try:
                    page.locator('.bottom_download_btn').click()
                except Exception:
                    pass
                page.wait_for_timeout(500)
                sign = ''
                timestamp = 0
                for _ in range(20):
                    data = page.evaluate("""(() => {
                        const localsSign = (window.locals && window.locals.get) ? window.locals.get('sign') : '';
                        const localsTimestamp = (window.locals && window.locals.get) ? window.locals.get('timestamp') : 0;
                        return {
                            sign: localsSign || '',
                            timestamp: localsTimestamp || 0
                        };
                    })()""")
                    if data:
                        sign = data.get('sign') or ''
                        ts = data.get('timestamp') or 0
                        try:
                            timestamp = int(ts)
                        except Exception:
                            timestamp = 0
                    if sign and timestamp:
                        break
                    page.wait_for_timeout(500)
                context.close()
            finally:
                browser.close()
    if not sign or not timestamp:
        raise RuntimeError('无法获取直链签名')
    return {'sign': sign, 'timestamp': timestamp}


def fetch_sharedownload_via_playwright(share_url, password, share_info, fs_id, randsk=None):
    if not share_info:
        raise RuntimeError('缺少分享信息，无法获取直链')
    uk = share_info.get('uk')
    share_id = share_info.get('shareid')
    if not uk or not share_id:
        raise RuntimeError('分享信息不完整，无法获取直链')

    extra_sekey = urllib.parse.unquote(randsk or '') if randsk else ''

    with playwright_lock:
        with sync_playwright() as playwright:
            launch_kwargs = {'headless': True, 'args': ['--no-sandbox']}
            if PLAYWRIGHT_PROXY:
                launch_kwargs['proxy'] = {'server': PLAYWRIGHT_PROXY}
            browser = playwright.chromium.launch(**launch_kwargs)
            context = None
            try:
                context = browser.new_context(user_agent=BROWSER_UA)
                cookies: list[dict[str, Any]] = [
                    {'name': 'BDUSS', 'value': BDUSS, 'domain': '.baidu.com', 'path': '/'},
                    {'name': 'BDUSS_BFESS', 'value': BDUSS, 'domain': '.baidu.com', 'path': '/'}
                ]
                if STOKEN:
                    cookies.append({'name': 'STOKEN', 'value': STOKEN, 'domain': '.baidu.com', 'path': '/'})
                context.add_cookies(cookies)  # type: ignore[arg-type]
                page = context.new_page()
                page.goto(share_url, wait_until='domcontentloaded')
                if password:
                    try:
                        page.fill('input#accessCode', password)
                        page.keyboard.press('Enter')
                    except Exception:
                        pass
                page.wait_for_timeout(2000)
                try:
                    page.locator('.bottom_download_btn').click()
                except Exception:
                    pass
                page.wait_for_timeout(500)
                js_args = {
                    'fsId': int(fs_id),
                    'uk': str(uk),
                    'shareId': str(share_id),
                    'sekey': extra_sekey,
                }
                result = page.evaluate("""async ({fsId, uk, shareId, sekey}) => {
                    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
                    const readLocals = () => {
                        const locals = (window.locals && window.locals.get) ? window.locals : null;
                        const signVal = locals ? locals.get('sign') : '';
                        const tsVal = locals ? locals.get('timestamp') : 0;
                        return {
                            sign: signVal || '',
                            timestamp: tsVal || 0
                        };
                    };
                    let localsData = readLocals();
                    let attempts = 0;
                    while ((!localsData.sign || !localsData.timestamp) && attempts < 60) {
                        await sleep(250);
                        localsData = readLocals();
                        attempts += 1;
                    }
                    if (!localsData.sign || !localsData.timestamp) {
                        return { error: 'SIGN_TIMEOUT' };
                    }
                    let sekeyValue = sekey || '';
                    if (!sekeyValue) {
                        const match = document.cookie.match(/BDCLND=([^;]+)/);
                        if (match && match[1]) {
                            try {
                                sekeyValue = decodeURIComponent(match[1]);
                            } catch (err) {
                                sekeyValue = match[1];
                            }
                        }
                    }
                    const query = new URLSearchParams({
                        app_id: '250528',
                        channel: 'chunlei',
                        clienttype: '12',
                        web: '1',
                        sign: localsData.sign,
                        timestamp: String(localsData.timestamp)
                    });
                    const body = new URLSearchParams();
                    body.set('encrypt', '0');
                    body.set('extra', JSON.stringify({ sekey: sekeyValue || '' }));
                    body.set('fid_list', JSON.stringify([fsId]));
                    body.set('primaryid', shareId);
                    body.set('uk', uk);
                    body.set('product', 'share');
                    body.set('type', 'dlink');
                    try {
                        const response = await fetch(`https://pan.baidu.com/api/sharedownload?${query.toString()}`, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                'Origin': 'https://pan.baidu.com',
                                'Referer': window.location.href,
                                'X-Requested-With': 'XMLHttpRequest'
                            },
                            body: body.toString()
                        });
                        const text = await response.text();
                        let data;
                        try {
                            data = JSON.parse(text);
                        } catch (err) {
                            data = { raw: text };
                        }
                        return {
                            status: response.status,
                            data,
                            sign: localsData.sign,
                            timestamp: localsData.timestamp,
                            sekey: sekeyValue || ''
                        };
                    } catch (err) {
                        return { error: err && err.message ? err.message : 'FETCH_FAILED' };
                    }
                }""", js_args)
                return result
            finally:
                if context:
                    context.close()
                browser.close()

    raise RuntimeError('Playwright 未返回直链结果')


def request_sharedownload_link(session, share_info, fs_id, randsk, sign_data, captcha_info=None, path=None):
    params = {
        'app_id': 250528,
        'channel': 'chunlei',
        'clienttype': 0,
        'web': 1,
        'sign': sign_data['sign'],
        'timestamp': sign_data['timestamp']
    }
    extra = {}
    if randsk:
        extra['sekey'] = urllib.parse.unquote(randsk)
    if captcha_info:
        if captcha_info.get('code'):
            extra['input'] = captcha_info['code']
        if captcha_info.get('vcode'):
            extra['vcode_str'] = captcha_info['vcode']
    data = {
        'encrypt': 1,
        'extra': json.dumps(extra or {'sekey': ''}, ensure_ascii=False),
        'fid_list': json.dumps([fs_id]),
        'primaryid': share_info['shareid'],
        'uk': share_info['uk'],
        'product': 'share',
        'timestamp': sign_data['timestamp']
    }
    if path:
        data['path_list'] = json.dumps([path])
    resp = session.post('https://pan.baidu.com/api/sharedownload', params=params, data=data, timeout=30)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    errno = body.get('errno', 0)
    if errno == 0:
        return body
    if errno in (9019, -62, -20, 50026):
        print(f"[sharedownload] need verify errno={errno}, body={body}")
        raise NeedVerificationError(body.get('show_msg') or body.get('errmsg') or 'need verify')
    else:
        print(f"[sharedownload] error errno={errno}, body={body}")
    raise RuntimeError(body.get('show_msg') or body.get('errmsg') or '直链获取失败')


def download_from_dlink(session, dlink, filename):
    headers = {
        'User-Agent': 'netdisk;2.2.51.6;netdisk;10.0.63;PC;android-android',
        'Referer': 'https://pan.baidu.com/disk/home'
    }
    local_path = DOWNLOAD_PATH / sanitize_segment(filename)
    with session.get(dlink, headers=headers, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(local_path, 'wb') as fh:
            for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    fh.write(chunk)
    return local_path


def download_dlink_to_path(session, dlink, destination):
    headers = {
        'User-Agent': 'netdisk;2.2.51.6;netdisk;10.0.63;PC;android-android',
        'Referer': 'https://pan.baidu.com/disk/home'
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with session.get(dlink, headers=headers, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(destination, 'wb') as fh:
            for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    fh.write(chunk)
    return destination


def download_direct_via_playwright(session, share_url, password, task_id, share_info, file_item, randsk):
    try:
        upsert_task(task_id, status='transferring', progress=15, message='尝试直链下载...')
        fs_id = int(file_item.get('fs_id'))
        direct_info = None
        direct_error = None
        try:
            playwright_payload = fetch_sharedownload_via_playwright(share_url, password, share_info, fs_id, randsk)
            if not playwright_payload:
                raise RuntimeError('Playwright 未返回直链结果')
            if isinstance(playwright_payload, dict) and playwright_payload.get('error'):
                raise RuntimeError(playwright_payload.get('error'))
            direct_info = (playwright_payload or {}).get('data') if isinstance(playwright_payload, dict) else None
            if not isinstance(direct_info, dict):
                raise RuntimeError('直链响应为空')
            if direct_info.get('errno') != 0:
                raise RuntimeError(direct_info.get('show_msg') or direct_info.get('errmsg') or f"直链失败 errno={direct_info.get('errno')}")
        except Exception as exc:
            direct_error = exc

        if isinstance(direct_info, dict) and direct_info.get('errno') == 0:
            entries = direct_info.get('list') or direct_info.get('urls') or []
            if entries:
                dlink = entries[0].get('dlink') or entries[0].get('url')
                if dlink:
                    filename = file_item.get('server_filename', 'unknown')
                    upsert_task(task_id, status='downloading', progress=60, filename=filename, message='直链下载中...')
                    local_file = download_from_dlink(session, dlink, filename)
                    upsert_task(task_id, status='completed', progress=100, filename=local_file.name, message='直链下载完成')
                    return True
                direct_error = RuntimeError('未获取到直链地址')
            else:
                direct_error = RuntimeError('直链为空')
        elif isinstance(direct_info, dict) and direct_info.get('errno') in (9019, -62, -20, 50026):
            raise NeedVerificationError(direct_info.get('show_msg') or direct_info.get('errmsg') or 'need verify')

        # Playwright fetch failed, fallback to manual API call using sign/timestamp
        if direct_error:
            print(f'Playwright sharedownload failed, fallback to manual API: {direct_error}')
        sign_data = fetch_sign_via_playwright(share_url, password)
        captcha_payload = None
        file_path = file_item.get('path') or file_item.get('server_path')
        while True:
            try:
                info = request_sharedownload_link(session, share_info, fs_id, randsk, sign_data, captcha_info=captcha_payload, path=file_path)
                break
            except NeedVerificationError:
                if captcha_payload is not None:
                    raise
                captcha_payload = attempt_auto_verification(task_id, share_url, session)
                if not captcha_payload:
                    raise
        entries = info.get('list') or info.get('urls') or []
        if not entries:
            raise RuntimeError('直链为空')
        dlink = entries[0].get('dlink') or entries[0].get('url')
        if not dlink:
            raise RuntimeError('未获取到直链地址')
        filename = file_item.get('server_filename', 'unknown')
        upsert_task(task_id, status='downloading', progress=60, filename=filename, message='直链下载中...')
        local_file = download_from_dlink(session, dlink, filename)
        upsert_task(task_id, status='completed', progress=100, filename=local_file.name, message='直链下载完成')
        return True
    except NeedVerificationError:
        mark_verification_required(task_id, share_url, session=session)
        return 'verify'
    except Exception as exc:
        upsert_task(task_id, status='queued', progress=0, message=f'直链不可用：{exc}')
        return False


def download_share_files_via_api(session, share_url, password, task_id, share_info, files, randsk):
    if not files:
        return False
    try:
        sign_data = fetch_sign_via_playwright(share_url, password)
    except Exception as exc:
        upsert_task(task_id, status='queued', progress=0, message=f'获取签名失败：{exc}')
        return False

    base_dir = DOWNLOAD_PATH / f"share_{sanitize_segment(share_info.get('surl', ''))}_{task_id}"
    total = len(files)
    for index, item in enumerate(files, start=1):
        filename = item.get('name') or f'file_{index}'
        relative = item.get('relative') or filename
        progress = 20 + int((index - 1) / max(total, 1) * 50)
        upsert_task(task_id, status='downloading', progress=progress, filename=filename, message=f'直链下载 {index}/{total} ...')
        try:
            fs_id = int(item.get('fs_id'))
        except (TypeError, ValueError):
            upsert_task(task_id, status='queued', progress=progress, message=f'跳过无效文件 {filename}')
            continue
        captcha_payload = None
        file_path = item.get('path')
        while True:
            try:
                info = request_sharedownload_link(session, share_info, fs_id, randsk, sign_data, captcha_info=captcha_payload, path=file_path)
                break
            except NeedVerificationError:
                if captcha_payload is not None:
                    mark_verification_required(task_id, share_url, session=session)
                    return 'verify'
                captcha_payload = attempt_auto_verification(task_id, share_url, session)
                if not captcha_payload:
                    return 'verify'
        entries = info.get('list') or info.get('urls') or []
        if not entries:
            upsert_task(task_id, status='queued', progress=progress, message=f'未获取到 {filename} 直链，跳过')
            continue
        dlink = entries[0].get('dlink') or entries[0].get('url')
        if not dlink:
            upsert_task(task_id, status='queued', progress=progress, message=f'未获取到 {filename} 下载地址，跳过')
            continue
        destination = build_local_path(base_dir, relative, filename)
        download_dlink_to_path(session, dlink, destination)

    upsert_task(
        task_id,
        status='completed',
        progress=100,
        filename=base_dir.name,
        message=f'直链下载完成，共 {total} 个文件'
    )
    return True


def extract_quark_share_code(share_url):
    if not share_url:
        return None
    match = re.search(r'pan\.quark\.cn/s/([A-Za-z0-9]+)', share_url)
    if match:
        return match.group(1)
    return None


def build_quark_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': QUARK_USER_AGENT,
        'Referer': 'https://pan.quark.cn/'
    })
    if REQUESTS_PROXIES:
        session.proxies.update(REQUESTS_PROXIES)
    if QUARK_COOKIE:
        session.headers['Cookie'] = QUARK_COOKIE
    return session


def quark_api_request(session, method, path, **kwargs):
    url = f"{QUARK_API_BASE}{path}{QUARK_COMMON_QUERY}"
    resp = session.request(method, url, timeout=QUARK_TIMEOUT, **kwargs)
    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError('夸克接口返回非 JSON 数据') from exc
    code = body.get('code', 0)
    if code not in (0, 200):
        raise RuntimeError(body.get('message') or f'夸克接口异常 (code={code})')
    return body.get('data') or {}, body


def quark_share_token(session, share_code, passcode):
    payload = {
        'pwd_id': share_code,
        'passcode': passcode or '',
        'support_visit_limit_private_share': True
    }
    data, _ = quark_api_request(session, 'POST', '/1/clouddrive/share/sharepage/token', json=payload)
    stoken = data.get('stoken')
    if not stoken:
        raise RuntimeError('夸克未返回 stoken')
    return stoken, data


def quark_fetch_dir(session, share_code, stoken, pdir_fid='0', page=1):
    params = {
        'ver': '2',
        'pwd_id': share_code,
        'stoken': stoken,
        'pdir_fid': pdir_fid,
        'force': '0',
        '_page': str(page),
        '_size': '200',
        '_fetch_banner': '1',
        '_fetch_share': '1',
        'fetch_relate_conversation': '1',
        '_fetch_total': '1',
        '_sort': 'file_type:asc,file_name:asc'
    }
    data, _ = quark_api_request(session, 'GET', '/1/clouddrive/share/sharepage/detail', params=params)
    return data or {}


def quark_collect_files(session, share_code, stoken):
    files = []
    queue = [('0', '')]
    share_info = None
    visited = set()
    while queue:
        dir_fid, relative = queue.pop(0)
        key = (dir_fid, relative)
        if key in visited:
            continue
        visited.add(key)
        data = quark_fetch_dir(session, share_code, stoken, pdir_fid=dir_fid)
        if share_info is None:
            share_info = data.get('share') or {}
        for item in data.get('list', []):
            name = item.get('file_name') or '文件'
            next_relative = f"{relative}/{name}" if relative else name
            if item.get('dir') or str(item.get('dir')) == '1':
                queue.append((item.get('fid'), next_relative))
            elif item.get('file') or str(item.get('file')) == '1':
                files.append({
                    'fid': item.get('fid'),
                    'name': name,
                    'relative': next_relative,
                    'size': int(item.get('size') or 0),
                    'share_fid_token': item.get('share_fid_token')
                })
    return share_info or {}, files


def sort_quark_files(files):
    if not files:
        return []
    return sorted(
        files,
        key=lambda item: (item.get('relative') or item.get('name') or '').lower()
    )


def extract_quark_download_url(payload):
    urls = []

    def walk(value):
        if isinstance(value, str) and value.startswith('http'):
            urls.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    for url in urls:
        if 'download' in url or 'dl-' in url:
            return url
    return urls[0] if urls else None


def quark_save_share(session, share_code, stoken):
    if not QUARK_TARGET_FOLDER_FID:
        raise RuntimeError('未配置 QUARK_TARGET_FOLDER_FID 环境变量，无法转存夸克文件')
    payload = {
        'pwd_id': share_code,
        'stoken': stoken,
        'pdir_fid': '0',
        'to_pdir_fid': QUARK_TARGET_FOLDER_FID,
        'pdir_save_all': True,
        'scene': 'link'
    }
    url = f"{QUARK_API_BASE}/1/clouddrive/share/sharepage/save{QUARK_COMMON_QUERY}"
    resp = session.post(url, json=payload, timeout=QUARK_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get('code') not in (0, 200):
        raise RuntimeError(data.get('message') or '夸克转存失败')
    task_id = (data.get('data') or {}).get('task_id')
    if not task_id:
        raise RuntimeError('夸克转存未返回任务 ID')
    return task_id


def quark_poll_task(session, task_id, max_attempts=20, delay=1.0):
    url = f"{QUARK_API_BASE}/1/clouddrive/task{QUARK_COMMON_QUERY}"
    for retry in range(max_attempts):
        params = {'task_id': task_id, 'retry_index': retry}
        resp = session.get(url, params=params, timeout=QUARK_TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        data = body.get('data') or {}
        status = data.get('status')
        if status == 2:
            save_as = data.get('save_as') or {}
            fids = save_as.get('save_as_top_fids') or save_as.get('save_as_select_top_fids') or []
            if fids:
                return fids
            raise RuntimeError('夸克转存任务完成但未返回文件 FID')
        time.sleep(delay)
    raise RuntimeError('夸克转存任务长时间无响应，请稍后重试')


BROWSER_UA = os.environ.get(
    'BAIDU_BROWSER_UA',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
QUARK_USER_AGENT = os.environ.get('QUARK_USER_AGENT', BROWSER_UA)
STOKEN = os.environ.get('STOKEN', '7187540b0e54c3a51e595b5fda498896781ae1b91d1e7850e2baa5a476fa1f27')
REMOTE_DIR = os.environ.get('REMOTE_DIR', '/pansou-download')


def create_baidu_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': BROWSER_UA,
        'Referer': 'https://pan.baidu.com/disk/home',
        'Accept-Encoding': 'identity'
    })
    if REQUESTS_PROXIES:
        session.proxies.update(REQUESTS_PROXIES)
    session.cookies.set('BDUSS', BDUSS, domain='.baidu.com')
    session.cookies.set('BDUSS_BFESS', BDUSS, domain='.baidu.com')
    if STOKEN:
        session.cookies.set('STOKEN', STOKEN, domain='.baidu.com')
    return session


def build_logid(session):
    baiduid = session.cookies.get('BAIDUID') or session.cookies.get('BAIDUID_BFESS')
    if baiduid:
        source = f"{baiduid}:FG=1"
    else:
        source = uuid.uuid4().hex.upper()
    return base64.b64encode(source.encode()).decode()


def get_share_info(session, share_url, password):
    match = re.search(r'/s/([A-Za-z0-9_-]+)', share_url)
    if not match:
        return None

    surl = normalize_surl(match.group(1))

    init_url = f'https://pan.baidu.com/share/init?surl={surl}'
    if password:
        init_url += f'&pwd={password}'

    resp = session.get(init_url, timeout=30)
    uk_match = re.search(r'"uk"\s*:\s*"?(\d+)"?', resp.text)
    shareid_match = re.search(r'"shareid"\s*:\s*"?(\d+)"?', resp.text)
    bdstoken_match = re.search(r'"bdstoken"\s*:\s*"([a-z0-9]+)"', resp.text)
    share_uk_match = re.search(r'"share_uk"\s*:\s*"?(\d+)"?', resp.text)
    dp_logid_match = re.search(r'dp-logid=(\d+)', resp.text)

    if not uk_match or not shareid_match or not bdstoken_match:
        return None

    return {
        'surl': surl,
        'uk': uk_match.group(1),
        'shareid': shareid_match.group(1),
        'bdstoken': bdstoken_match.group(1),
        'dp_logid': dp_logid_match.group(1) if dp_logid_match else None,
        'share_uk': share_uk_match.group(1) if share_uk_match else None
    }


def fetch_share_cookie(share_url, password):
    try:
        with playwright_lock:
            with sync_playwright() as playwright:
                launch_kwargs = {'headless': True, 'args': ['--no-sandbox']}
                if PLAYWRIGHT_PROXY:
                    launch_kwargs['proxy'] = {'server': PLAYWRIGHT_PROXY}
                browser = playwright.chromium.launch(**launch_kwargs)
                page = browser.new_page()
                page.goto(share_url, wait_until='networkidle')
                if password:
                    page.fill('input#accessCode', password)
                    page.keyboard.press('Enter')
                page.wait_for_timeout(3000)
                cookies = page.context.cookies()
                browser.close()
        for cookie in cookies:
            if cookie.get('name') == 'BDCLND':
                return cookie.get('value')
    except Exception as exc:
        print(f'Share cookie fetch failed: {exc}')
    return None


def ensure_share_verified(session, share_url, password):
    match = re.search(r'/s/([A-Za-z0-9_-]+)', share_url or '')
    if not match:
        return None
    surl = normalize_surl(match.group(1))
    payload = {'pwd': password or ''}
    headers = {'Referer': share_url}
    try:
        resp = session.post(f'https://pan.baidu.com/share/verify?surl={surl}', data=payload, headers=headers, timeout=30)
        data = resp.json()
    except Exception:
        data = {}
    if data.get('errno') == 0:
        randsk = data.get('randsk') or data.get('sekey')
        if randsk:
            session.cookies.set('BDCLND', randsk, domain='pan.baidu.com')
        return randsk
    cookie = fetch_share_cookie(share_url, password)
    if cookie:
        session.cookies.set('BDCLND', cookie, domain='pan.baidu.com')
        return cookie
    return None


def get_account_bdstoken(session):
    try:
        resp = session.get('https://pan.baidu.com/disk/home', timeout=30)
        match = re.search(r'"bdstoken":"([a-z0-9]+)"', resp.text)
        if match:
            return match.group(1)
    except Exception:
        return None
    return None


def share_list(session, surl, password, dir_path, root):
    params = {
        'shorturl': surl,
        'root': root,
        'dir': dir_path,
        'pwd': password or '',
        'web': 1,
        'channel': 'chunlei',
        'app_id': 250528,
        'clienttype': 0
    }
    resp = session.get('https://pan.baidu.com/share/list', params=params, timeout=30)
    try:
        data = resp.json()
    except ValueError:
        return []
    return data.get('list', [])


def fetch_first_file(session, surl, password):
    queue = [('/', 1)]
    visited = set()
    while queue:
        dir_path, root = queue.pop(0)
        key = (dir_path, root)
        if key in visited:
            continue
        visited.add(key)
        items = share_list(session, surl, password, dir_path, root)
        for item in items:
            is_dir_flag = item.get('isdir')
            if is_dir_flag is None:
                is_dir_flag = item.get('is_dir')
            if str(is_dir_flag) == '1' and item.get('path'):
                queue.append((item['path'], 0))
            else:
                return item
    return None


def collect_share_files(session, surl, password, limit=200):
    files = []
    queue = [('/', 1, '')]
    visited = set()
    while queue and len(files) < limit:
        dir_path, root, relative_base = queue.pop(0)
        key = (dir_path, root, relative_base)
        if key in visited:
            continue
        visited.add(key)
        items = share_list(session, surl, password, dir_path, root)
        for item in items:
            name = item.get('server_filename') or item.get('filename') or item.get('title') or '未命名文件'
            is_dir_flag = item.get('isdir')
            if is_dir_flag is None:
                is_dir_flag = item.get('is_dir')
            if str(is_dir_flag) == '1' and item.get('path'):
                next_relative = f"{relative_base}/{name}" if relative_base else name
                queue.append((item['path'], 0, next_relative))
                continue
            fs_id = item.get('fs_id')
            if not fs_id:
                continue
            relative = f"{relative_base}/{name}" if relative_base else name
            files.append({
                'fs_id': fs_id,
                'name': name,
                'relative': relative,
                'size': item.get('size') or 0,
                'path': item.get('path')
            })
            if len(files) >= limit:
                break
    return files


def ensure_remote_dir(session, bdstoken, path):
    if path == '/':
        return
    if not bdstoken:
        raise RuntimeError('无法获取账号 bdstoken，无法创建目录')
    params = {
        'method': 'list',
        'dir': path,
        'bdstoken': bdstoken,
        'order': 'name',
        'start': 0,
        'limit': 10
    }
    resp = session.get('https://pan.baidu.com/rest/2.0/xpan/file', params=params, timeout=30)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if data.get('errno') == 0:
        return
    create_params = {
        'method': 'create',
        'bdstoken': bdstoken,
        'dir': path,
    }
    create_data = {
        'path': path,
        'isdir': 1,
        'size': 0,
        'block_list': json.dumps([]),
        'mode': '0'
    }
    session.post('https://pan.baidu.com/rest/2.0/xpan/file', params=create_params, data=create_data, timeout=30)


def list_remote_dir(session, bdstoken, directory):
    if not bdstoken:
        return []
    entries = []
    start = 0
    limit = 200
    while True:
        params = {
            'method': 'list',
            'dir': directory,
            'bdstoken': bdstoken,
            'order': 'time',
            'start': start,
            'limit': limit,
            'desc': 1,
            't': int(time.time())
        }
        resp = session.get('https://pan.baidu.com/rest/2.0/xpan/file', params=params, timeout=30)
        try:
            data = resp.json()
        except ValueError:
            break
        if data.get('errno') != 0:
            break
        chunk = data.get('list', [])
        entries.extend(chunk)
        if len(chunk) < limit:
            break
        start += limit
    return entries


def wait_for_remote_file(session, bdstoken, filename, task_id, directory=None, timeout=600, interval=5):
    start = time.time()
    deadline = start + timeout
    attempts = 0
    progress_base = 30
    progress_span = 20
    base_dir = directory or REMOTE_DIR
    while time.time() < deadline:
        attempts += 1
        queue = [base_dir]
        visited = set()
        found_entry = None
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            items = list_remote_dir(session, bdstoken, current)
            for entry in items:
                is_dir_flag = entry.get('isdir')
                if is_dir_flag is None:
                    is_dir_flag = entry.get('is_dir')
                if str(is_dir_flag) == '1':
                    path = entry.get('path')
                    if path:
                        queue.append(path)
                elif entry.get('server_filename') == filename:
                    found_entry = entry
                    queue = []
                    break
        if found_entry:
            elapsed = int(time.time() - start)
            upsert_task(
                task_id,
                status='transferring',
                progress=progress_base + progress_span,
                message=f'文件已同步到网盘，用时 {elapsed}s，准备下载...'
            )
            return found_entry
        elapsed = int(time.time() - start)
        progress = min(progress_base + int(progress_span * elapsed / max(timeout, 1)), progress_base + progress_span - 1)
        upsert_task(
            task_id,
            status='transferring',
            progress=progress,
            message=f'等待文件同步到网盘... 已 {elapsed}s，刷新第 {attempts} 次'
        )
        time.sleep(interval)
    return None


def download_remote_file(session, remote_path, local_name):
    located_url = f"https://pan.baidu.com/api/file?app_id=266719&method=located&path={urllib.parse.quote(remote_path)}"
    resp = session.get(located_url, timeout=30)
    data = resp.json()
    dlink = data.get('dlink')
    if not dlink:
        raise RuntimeError('获取下载链接失败')

    local_path = DOWNLOAD_PATH / local_name
    with session.get(dlink, stream=True, timeout=600) as download_resp:
        download_resp.raise_for_status()
        with open(local_path, 'wb') as fh:
            for chunk in download_resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
    return local_path


def download_via_alist(share_url, password, task_id, sekey=None):
    client = get_alist_client()
    if not client:
        raise RuntimeError('未配置 Alist 接入')
    surl = extract_surl(share_url)
    if not surl:
        raise RuntimeError('无法解析分享识别码')

    print(f'[Alist] task {task_id} 尝试挂载分享 {surl}', flush=True)
    upsert_task(task_id, status='transferring', progress=5, message='正在通过 Alist 更新分享挂载...')
    client.mount_share(surl, password or '', sekey=sekey)

    upsert_task(task_id, status='transferring', progress=15, message='正在获取分享文件列表...')
    files = client.collect_files(ALIST_SHARE_MOUNT, refresh=True)
    if not files:
        raise RuntimeError('分享中未发现文件')

    safe_surl = sanitize_segment(surl)
    base_dir = DOWNLOAD_PATH / f"share_{safe_surl}_{task_id}"
    total = len(files)
    for index, entry in enumerate(files, start=1):
        progress = 15 + int((index - 1) / total * 75)
        message = f"正在获取 {entry['name']} 下载地址 ({index}/{total})"
        upsert_task(task_id, status='downloading', progress=progress, filename=entry['name'], message=message)
        info = client.get_download_info(entry['path'])
        download_url = (info or {}).get('raw_url') or (info or {}).get('url')
        if download_url and download_url.startswith('/'):
            download_url = f"{ALIST_BASE}{download_url}"
        headers = (info or {}).get('headers') or (info or {}).get('header')
        local_path = build_local_path(base_dir, entry.get('relative'), entry['name'])
        download_stream_to_file(
            download_url,
            headers,
            local_path,
            task_id=task_id,
            total_bytes=int(entry.get('size') or 0)
        )

    upsert_task(
        task_id,
        status='completed',
        progress=100,
        filename=base_dir.name,
        message=f'Alist 下载完成，共 {total} 个文件'
    )
    return True


def download_from_alist_netdisk(client, remote_path, filename, task_id=None):
    if not client or not remote_path or not ALIST_NETDISK_MOUNT:
        return None
    mount_base = PurePosixPath(ALIST_NETDISK_MOUNT)
    remote_posix = PurePosixPath(remote_path)
    try:
        relative = remote_posix.relative_to(PurePosixPath(REMOTE_DIR))
    except ValueError:
        relative = remote_posix.relative_to('/') if remote_posix.is_absolute() else remote_posix
    alist_path = str(mount_base / relative)
    parent = str(PurePosixPath(alist_path).parent) or '/'
    client.list_dir(parent, refresh=True)
    info = client.get_download_info(alist_path)
    download_url = (info or {}).get('raw_url') or (info or {}).get('url')
    if download_url and download_url.startswith('/'):
        download_url = f"{ALIST_BASE}{download_url}"
    headers = (info or {}).get('headers') or (info or {}).get('header')
    local_path = DOWNLOAD_PATH / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)
    total_bytes = int((info or {}).get('size') or 0)
    download_stream_to_file(download_url, headers, local_path, task_id=task_id, total_bytes=total_bytes)
    return local_path

def transfer_and_download(share_url, password, task_id):
    try:
        session = create_baidu_session()
        upsert_task(task_id, status='transferring', progress=5, message='获取分享信息...')
        randsk = ensure_share_verified(session, share_url, password)

        share_info = get_share_info(session, share_url, password)
        if not share_info:
            upsert_task(task_id, status='error', progress=0, message='无法获取分享信息，请检查链接和密码')
            return

        collected_files = collect_share_files(session, share_info['surl'], password)
        file_item = collected_files[0] if collected_files else fetch_first_file(session, share_info['surl'], password)
        if not file_item:
            upsert_task(task_id, status='error', progress=0, message='分享链接中没有可转存的文件')
            return

        if len(collected_files) > 1:
            result = download_share_files_via_api(session, share_url, password, task_id, share_info, collected_files, randsk)
            if result is True or result == 'verify':
                return

        alist_used = False
        if ALIST_DIRECT_DOWNLOAD:
            alist_client_instance = get_alist_client()
            if alist_client_instance:
                try:
                    download_via_alist(share_url, password, task_id, sekey=randsk)
                    return
                except Exception as alist_exc:
                    print(f'[Alist] direct 下载失败: {alist_exc}', flush=True)
                    upsert_task(
                        task_id,
                        status='queued',
                        progress=0,
                        message=f'Alist 尝试失败，回退直连：{alist_exc}'
                    )
                    alist_used = True

        direct_result = download_direct_via_playwright(session, share_url, password, task_id, share_info, file_item, randsk)
        if direct_result is True or direct_result == 'verify':
            return

        if not alist_used and ALIST_DIRECT_DOWNLOAD:
            alist_client_instance = get_alist_client()
            if alist_client_instance:
                try:
                    download_via_alist(share_url, password, task_id, sekey=randsk)
                    return
                except Exception as alist_exc:
                    print(f'[Alist] fallback 下载失败: {alist_exc}', flush=True)
                    upsert_task(
                        task_id,
                        status='queued',
                        progress=0,
                        message=f'Alist 尝试失败，回退直连：{alist_exc}'
                    )
                    alist_used = True

        if randsk and 'randsk' not in share_info:
            share_info['randsk'] = randsk

        account_bdstoken = get_account_bdstoken(session)
        bdstoken = account_bdstoken or share_info.get('bdstoken')
        if not bdstoken:
            upsert_task(task_id, status='error', progress=0, message='无法获取账号凭据 (bdstoken)')
            return

        filename = file_item.get('server_filename', 'unknown')
        fs_id = int(file_item.get('fs_id'))

        task_dir = REMOTE_DIR
        ensure_remote_dir(session, bdstoken, task_dir)

        dp_logid = share_info.get('dp_logid') or str(int(time.time() * 1000))
        sekey_value = share_info.get('randsk') or randsk
        params = {
            'shareid': share_info['shareid'],
            'from': share_info['uk'],
            'bdstoken': bdstoken,
            'channel': 'chunlei',
            'web': 1,
            'clienttype': 0,
            'app_id': 250528,
            'dp-logid': dp_logid,
            'logid': build_logid(session)
        }
        if sekey_value:
            params['sekey'] = urllib.parse.unquote(sekey_value)
        data = {
            'fsidlist': json.dumps([fs_id]),
            'path': task_dir,
            'ondup': 'newcopy'
        }

        upsert_task(task_id, status='transferring', progress=20, message='转存文件到网盘...')
        headers = {
            'Origin': 'https://pan.baidu.com',
            'Referer': share_url,
            'User-Agent': BROWSER_UA,
            'X-Requested-With': 'XMLHttpRequest'
        }
        resp = session.post('https://pan.baidu.com/share/transfer', params=params, data=data, headers=headers, timeout=30)
        try:
            transfer_result = resp.json()
        except ValueError:
            content = resp.content.strip()
            if not content:
                transfer_result = {'errno': 0}
            else:
                try:
                    transfer_result = json.loads(content.decode('utf-8', errors='ignore'))
                except Exception:
                    transfer_result = {'errno': 0}

        errno = transfer_result.get('errno', 0)
        if errno not in (0, 2):
            err_msg = transfer_result.get('show_msg') or transfer_result.get('err_msg') or '转存失败'
            upsert_task(task_id, status='error', progress=0, message=err_msg)
            return
        upsert_task(task_id, status='transferring', progress=30, message='等待文件同步到网盘...')
        remote_entry = wait_for_remote_file(session, bdstoken, filename, task_id, directory=task_dir)
        if not remote_entry:
            upsert_task(task_id, status='error', progress=0, message='网盘长时间未出现该文件，可能转存失败')
            return

        remote_path = remote_entry.get('path')
        resolved_filename = remote_entry.get('server_filename', filename)
        upsert_task(task_id, status='downloading', progress=55, filename=resolved_filename, message='获取下载链接...')
        local_file = None
        alist_for_netdisk = get_alist_client()
        if alist_for_netdisk:
            try:
                local_file = download_from_alist_netdisk(alist_for_netdisk, remote_path, resolved_filename, task_id=task_id)
            except Exception as alist_dl_exc:
                print(f'Alist netdisk 下载失败: {alist_dl_exc}')
        if local_file is None:
            local_file = download_remote_file(session, remote_path, resolved_filename)

        upsert_task(
            task_id,
            status='completed',
            progress=100,
            filename=local_file.name,
            message='下载完成'
        )

    except Exception as e:
        upsert_task(task_id, status='error', progress=0, message=str(e))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/search', methods=['POST'])
def search():
    kw = (request.json or {}).get('kw', '')
    if not kw:
        return jsonify({'error': '请输入关键词'})
    
    try:
        payload = {
            'kw': kw,
            'res': 'merge',
            'cloud_types': ['baidu', 'aliyun', 'quark', 'xunlei', 'uc', 'mobile']
        }
        resp = requests.post(SEARCH_API, json=payload, timeout=30)
        data = resp.json()
        return jsonify(data.get('data', {}))
    except Exception as e:
        return jsonify({'error': str(e)}), 502

PROVIDER_HANDLERS = {
    'baidu': transfer_and_download,
    'aliyun': download_aliyun_share,
    'quark': download_quark_share,
}


@app.route('/api/download', methods=['POST'])
def download():
    data = request.json or {}
    url = (data.get('url') or '').strip()
    password = (data.get('password') or '').strip()
    provider = (data.get('provider') or 'baidu').lower()

    if not url:
        return jsonify({'status': 'error', 'error': '请输入链接'})

    handler = PROVIDER_HANDLERS.get(provider)
    if not handler:
        readable = '、'.join({'baidu': '百度网盘', 'aliyun': '阿里云盘', 'quark': '夸克网盘'}.get(p, p) for p in PROVIDER_HANDLERS.keys())
        return jsonify({'status': 'error', 'error': f'当前仅支持 {readable} 下载'})

    if provider == 'baidu' and 'pan.baidu.com' not in url:
        return jsonify({'status': 'error', 'error': '请输入有效的百度网盘链接'})
    if provider == 'aliyun' and not any(host in url for host in ('alipan.com', 'aliyundrive.com')):
        return jsonify({'status': 'error', 'error': '请输入有效的阿里云盘链接'})

    if not password:
        pwd_match = re.search(r'[?#&](?:pwd|password)[=:]?([A-Za-z0-9]{4})', url, re.IGNORECASE)
        if pwd_match:
            password = pwd_match.group(1)

    task_id = str(uuid.uuid4())[:8]
    upsert_task(task_id, status='queued', progress=0, filename='', message='等待开始', provider=provider)
    threading.Thread(target=handler, args=(url, password, task_id), daemon=True).start()

    return jsonify({'status': 'ok', 'task_id': task_id})

@app.route('/api/progress/<task_id>')
def progress(task_id):
    task = get_task(task_id)
    if not task:
        return jsonify({'status': 'unknown'})
    return jsonify(task)


@app.route('/api/tasks')
def tasks():
    return jsonify({'tasks': list_tasks()})

@app.route('/api/files')
def list_files():
    files = []
    if DOWNLOAD_PATH.exists():
        for path in DOWNLOAD_PATH.iterdir():
            if path.is_file():
                stat = path.stat()
                files.append({
                    'name': path.name,
                    'size': human_size(stat.st_size),
                    'modified': time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))
                })
    files.sort(key=lambda x: x['modified'], reverse=True)
    return jsonify({'files': files})


@app.route('/captures/<path:filename>')
def serve_capture(filename):
    try:
        return send_from_directory(CAPTURE_DIR, filename)
    except FileNotFoundError:
        return jsonify({'error': 'not found'}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8082)
