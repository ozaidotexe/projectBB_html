import logging
import asyncio
import random
import pytz
import os
import telegram
import sys
import urllib.parse
import mysql.connector
from flask import Flask, jsonify, request
from flask_cors import CORS
import threading
from time import time
from datetime import datetime, timedelta

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    WebAppInfo
)

from telegram.ext import (
    ApplicationBuilder, 
    ContextTypes, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    CallbackQueryHandler
)

# ==========================================
# 1. KONFIGURASI & DATABASE (FULL SQL)
# ==========================================
TOKEN_BOT = '7794349669:AAGNMNj1kL6GHy9bGIHzmYLpltS_ZbHqeOs'
DAFTAR_ADMIN = [1784997665] 
REK_ADMIN_UTAMA = "BANK SEABANK - 9012 2641 5713 - a/n Alvin Ardian"
HUBUNGI_ADMIN = "@none" 
REF_BONUS_POIN = 300
user_cooldowns = {} 
RATE_LIMIT_SECONDS = 3
tz_jkt = pytz.timezone('Asia/Jakarta')

# --- KONEKSI DATABASE ---
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="bot_p2p_hunting"
    )

def add_log(user_id, tipe_aksi, jumlah_poin, keterangan):
    conn = get_db()
    cursor = conn.cursor()
    try:
        query = """
            INSERT INTO logs (user_id, tipe_aksi, jumlah_poin, keterangan, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """
        # created_at menggunakan waktu Jakarta (tz_jkt)
        now = datetime.now(tz_jkt).replace(tzinfo=None)
        cursor.execute(query, (user_id, tipe_aksi, jumlah_poin, keterangan, now))
        conn.commit()
    except Exception as e:
        print(f"❌ Gagal menulis log: {e}")
    finally:
        cursor.close()
        conn.close()

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    # Tabel Users
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            points INT DEFAULT 100,
            rekening VARCHAR(255) DEFAULT 'Belum diatur',
            is_hunting TINYINT(1) DEFAULT 0,
            res_active VARCHAR(50),
            referred_by BIGINT,
            ref_reward_claimed TINYINT(1) DEFAULT 0,
            last_hunt_time DATETIME
        )
    """)
    
    # Tabel Admin (load_admins)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_admins (
            user_id BIGINT PRIMARY KEY,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Tabel Banned
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS banned (
            user_id BIGINT PRIMARY KEY,
            reason TEXT,
            ban_time DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Tabel User Assets
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_assets (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT,
            item_key VARCHAR(50),
            buy_price INT,
            buy_time DATETIME,
            is_selling TINYINT(1) DEFAULT 0
        )
    """)
    
    # Tabel Logs (Agar riwayat transaksi terisi)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT,
            tipe_aksi VARCHAR(50),
            jumlah_poin INT,
            keterangan TEXT,
            created_at DATETIME
        )
    """)

    # Tabel Items Config (Untuk memperbaiki error 1146 /edit)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items_config_db (
            item_key VARCHAR(50) PRIMARY KEY,
            name VARCHAR(100),
            start_time VARCHAR(10),
            end_time VARCHAR(10),
            price_min INT,
            price_max INT,
            profit_percent FLOAT,
            hold_days INT,
            point_now INT
        )
    """)
    
    conn.commit()
    cursor.close()
    conn.close()

items_config = {
    'ITEM_A': {
        'name': 'Ledger', 'min': 200000, 'max': 520000,
        'start_time': "19:00", 'end_time': "19:05",
        'point_res': 4, 'point_now': 8,
        'hold_days': 3, 'profit_percent': 0.10, 'is_locked': False, 'owners_queue': [], 'current_price': 200000
    },
    'ITEM_B': {
        'name': 'Glitch', 'min': 520010, 'max': 1000000,
        'start_time': "12:00", 'end_time': "12:05",
        'point_res': 5, 'point_now': 10,
        'hold_days': 2, 'profit_percent': 0.05, 'is_locked': False, 'owners_queue': [], 'current_price': 520010
    },
    'ITEM_C': {
        'name': 'Minty', 'min': 1000010, 'max': 3000000,
        'start_time': "17:00", 'end_time': "17:05",
        'point_res': 8, 'point_now': 16,
        'hold_days': 1, 'profit_percent': 0.04, 'is_locked': False, 'owners_queue': [], 'current_price': 1000010
    },
    'ITEM_D': {
        'name': 'Flops', 'min': 3000010, 'max': 4000000,
        'start_time': "14:00", 'end_time': "14:05",
        'point_res': 40, 'point_now': 80,
        'hold_days': 5, 'profit_percent': 0.12, 'is_locked': False, 'owners_queue': [], 'current_price': 3000010
    },
    'ITEM_E': {
        'name': 'Pips', 'min': 4000010, 'max': 6000000,
        'start_time': "21:00", 'end_time': "21:05",
        'point_res': 64, 'point_now': 128,
        'hold_days': 6, 'profit_percent': 0.15, 'is_locked': False, 'owners_queue': [], 'current_price': 4000010
    }
}

used_photos = set() 
active_trades = {} 

# --- FUNGSI AUTO-BAN SQL ---
def save_banned_user(uid, reason="Pelanggaran Sistem"):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT IGNORE INTO banned (user_id, reason) VALUES (%s, %s)", (uid, reason))
        conn.commit()
    except Exception as e:
        print(f"Error Save Banned: {e}")
    finally:
        cursor.close()
        conn.close()

def add_user_asset(user_id, item_key, price):
    conn = get_db()
    cursor = conn.cursor()
    query = "INSERT INTO user_assets (user_id, item_key, buy_price, buy_time) VALUES (%s, %s, %s, %s)"
    cursor.execute(query, (user_id, item_key, price, datetime.now(tz_jkt).replace(tzinfo=None)))
    conn.commit()
    cursor.close()
    conn.close()

def load_admins():
    global DAFTAR_ADMIN
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM bot_admins")
    rows = cursor.fetchall()
    
    # Ambil ID dari DB dan gabungkan dengan ID yang sudah ada di script (Owner)
    for row in rows:
        if row[0] not in DAFTAR_ADMIN:
            DAFTAR_ADMIN.append(row[0])
            
    cursor.close()
    conn.close()

# ==========================================
# 2. SISTEM AUTO-BAN & NOTIFIKASI (JOBS)
# ==========================================
async def job_market_notifier(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(tz_jkt).replace(tzinfo=None) 
    now_str = now.strftime("%H:%M")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        for t_key in ['ITEM_A', 'ITEM_B', 'ITEM_C', 'ITEM_D', 'ITEM_E']:
            conf = items_config[t_key]
            
            for p in conf['owners_queue'][:]:
                if p['id'] in DAFTAR_ADMIN: continue
                if p.get('is_locked') == True: continue
                
                days_held = (now - p['buy_time']).days
                if days_held >= conf['hold_days'] and conf['current_price'] >= conf['max']:
                    bid = p['id']
                    rek_user = p['rek']
                    
                    # --- MULAI LOGIKA KASTA ---
                    
                    if t_key in ['ITEM_A', 'ITEM_B', 'ITEM_D']:
                        target_map = {'ITEM_A': 'ITEM_B', 'ITEM_B': 'ITEM_C', 'ITEM_D': 'ITEM_E'}
                        target_tier = target_map[t_key]
                        target_conf = items_config[target_tier]
                        
                        cursor.execute("DELETE FROM user_assets WHERE id = %s", (p['asset_id'],))
                        
                        cursor.execute("""
                            INSERT INTO user_assets (user_id, item_key, buy_price, buy_time, is_selling) 
                            VALUES (%s, %s, %s, %s, 0)
                        """, (bid, target_tier, target_conf['min'], now))
                        
                        new_id = cursor.lastrowid
                        target_conf['owners_queue'].append({
                            'asset_id': new_id, 'id': bid, 'rek': rek_user, 'buy_time': now
                        })
                        
                        conf['owners_queue'].remove(p)
                        await context.bot.send_message(bid, f"🆙 **UPGRADE!**\nAset {conf['name']} Anda telah naik kasta menjadi {target_conf['name']}!")

                    elif t_key in ['ITEM_C', 'ITEM_E']:
                        saldo_pecah = conf['current_price']
                        pecahan_info = []
                        
                        list_pecahan = ['ITEM_D', 'ITEM_C', 'ITEM_B', 'ITEM_A'] if t_key == 'ITEM_E' else ['ITEM_B', 'ITEM_A']
                        
                        cursor.execute("DELETE FROM user_assets WHERE id = %s", (p['asset_id'],))
                        
                        for tier_kecil in list_pecahan:
                            c_kecil = items_config[tier_kecil]
                            harga_min_standar = c_kecil['min']
                            
                            if tier_kecil != 'ITEM_A':
                                while saldo_pecah >= harga_min_standar:
                                    instant_sell_time = now - timedelta(days=c_kecil['hold_days'])
                                    
                                    cursor.execute("""
                                        INSERT INTO user_assets (user_id, item_key, buy_price, buy_time, is_selling) 
                                        VALUES (%s, %s, %s, %s, 0)
                                    """, (bid, tier_kecil, harga_min_standar, instant_sell_time))
                                    
                                    new_id = cursor.lastrowid
                                    c_kecil['owners_queue'].append({
                                        'asset_id': new_id, 'id': bid, 'rek': rek_user, 'buy_time': instant_sell_time
                                    })
                                    
                                    saldo_pecah -= harga_min_standar
                                    pecahan_info.append(c_kecil['name'])
                            
                            else:
                                if saldo_pecah > 0:
                                    instant_sell_time = now - timedelta(days=c_kecil['hold_days'])
                                    
                                    cursor.execute("""
                                        INSERT INTO user_assets (user_id, item_key, buy_price, buy_time, is_selling) 
                                        VALUES (%s, %s, %s, %s, 0)
                                    """, (bid, 'ITEM_A', int(saldo_pecah), instant_sell_time))
                                    
                                    new_id = cursor.lastrowid
                                    c_kecil['owners_queue'].append({
                                        'asset_id': new_id, 'id': bid, 'rek': rek_user, 'buy_time': instant_sell_time
                                    })
                                    
                                    pecahan_info.append(f"{c_kecil['name']} (Rp{int(saldo_pecah):,})")
                                    saldo_pecah = 0
                        
                        
                        info_text = ", ".join(pecahan_info)
                        await context.bot.send_message(bid, f"♻️ **ASET MELEDAK!**\n{conf['name']} pecah menjadi: {info_text}")

                    conn.commit()
                    conf['owners_queue'].remove(p)
                    continue

    except Exception as e:
        print(f"❌ Error Notifier: {e}")
    finally:
        cursor.close()
        conn.close()

async def job_ban_pembeli(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    uid, item_key = data['uid'], data['item_key']
    
    if item_key in items_config:
        items_config[item_key]['is_locked'] = False
        print(f"🔓 [SYSTEM] Item {item_key} otomatis di-unlock (Timer Habis).")

    trade = active_trades.pop(uid, None)
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET is_hunting = 0 WHERE user_id = %s", (uid,))
        conn.commit()
        
        if not trade:
            cursor.close()
            conn.close()
            return

        if uid in DAFTAR_ADMIN:
            print(f"🛡️ [PROTEKSI] Admin {uid} terdeteksi telat konfirmasi, tapi tidak dibanned.")
        else:
            save_banned_user(uid, "Gagal kirim bukti transfer dalam 30 menit")
            await context.bot.send_message(
                chat_id=uid, 
                text="🚫 **BANNED!**\nAnda tidak mengirim bukti transfer tepat waktu (30 menit). Akun telah dinonaktifkan."
            )
            print(f"🚫 [BANNED] User {uid} diblokir karena timeout.")

        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"⚠️ [ERROR] Gagal menjalankan job_ban_pembeli: {e}")

async def job_ban_penjual(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    sid, bid = data['sid'], data['bid']
    
    trade = active_trades.pop(bid, None)
    
    if trade:
        item_key = trade['item_key']
        items_config[item_key]['is_locked'] = False
        
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET is_hunting = 0 WHERE user_id = %s", (bid,))
            conn.commit()
            cursor.close()
            conn.close()

            if sid in DAFTAR_ADMIN:
                print(f"🛡️ PROTEKSI: {sid} (Admin/Owner) tidak dibanned.")
                await context.bot.send_message(bid, "⏳ Penjual (Server) sedang memproses konfirmasi Anda secara manual.")
                return 

            save_banned_user(sid, "Gagal konfirmasi pembayaran tepat waktu")
            
            await context.bot.send_message(sid, "🚫 **BANNED!**\nAnda telat konfirmasi pembayaran.")
            await context.bot.send_message(bid, "⚠️ Penjual telat konfirmasi dan telah di-ban.\nHubungi Admin untuk klaim aset.")
        except Exception as e:
            print(f"⚠️ Gagal ban penjual {sid} di Database: {e}")

async def is_rate_limited(update: Update) -> bool:
    if not update.message or not update.effective_user: 
        return False
        
    uid = update.effective_user.id
    now = time()
    
    if uid in user_cooldowns:
        last_time = user_cooldowns[uid]
        if now - last_time < RATE_LIMIT_SECONDS:
            try:
                await update.message.reply_text("⏳ *Sabar...* Jangan terlalu cepat!", parse_mode='Markdown')
            except Exception as e:
                print(f"Gagal kirim pesan rate limit: {e}")
            return True
            
    user_cooldowns[uid] = now
    return False

async def clear_cooldown_cache(context: ContextTypes.DEFAULT_TYPE):
    """Membersihkan cache cooldown yang sudah lama agar RAM tetap lega."""
    now = time()
    to_delete = [uid for uid, timestamp in user_cooldowns.items() if now - timestamp > 60]
    for uid in to_delete:
        del user_cooldowns[uid]

# ==========================================
# 3. UTILS & ADMIN COMMANDS (FULL SQL)
# ==========================================
def is_owner(uid):
    return uid == DAFTAR_ADMIN[0]

async def check_user(update: Update):
    uid = update.effective_user.id
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT user_id FROM banned WHERE user_id = %s", (uid,))
    if cursor.fetchone():
        await update.message.reply_text("🚫 Akun Anda di-BANNED.")
        cursor.close()
        conn.close()
        return False
    cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (uid,))
    if not cursor.fetchone():
        await update.message.reply_text("⛔ Ketik /start dahulu.")
        cursor.close()
        conn.close()
        return False
    cursor.close()
    conn.close()
    return True

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    pesan_bc = " ".join(context.args)
    if not pesan_bc: return await update.message.reply_text("Format: /bc [pesan]")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    cursor.close()
    conn.close()

    berhasil, gagal = 0, 0
    msg = await update.message.reply_text(f"Mengirim ke {len(users)} user...")

    for row in users:
        try:
            await context.bot.send_message(chat_id=row['user_id'], text=f"ℹ️ **PENGUMUMAN**\n\n{pesan_bc}", parse_mode='Markdown')
            berhasil += 1
            await asyncio.sleep(0.05)
        except telegram.error.Forbidden:
            gagal += 1
        except Exception:
            gagal += 1

    await msg.edit_text(f"✅ Selesai.\nBerhasil: {berhasil}\nGagal (Blokir/Off): {gagal}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in DAFTAR_ADMIN: 
        return

    try:
        target = int(context.args[0])
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM banned WHERE user_id = %s", (target,))
        rows_deleted = cursor.rowcount
        
        cursor.execute("UPDATE users SET is_hunting = 0 WHERE user_id = %s", (target,))
        
        conn.commit()
        cursor.close()
        conn.close()

        if rows_deleted > 0:
            await update.message.reply_text(f"✅ **BERHASIL!**\nID `{target}` telah dihapus dari daftar Banned Database.")
        else:
            await update.message.reply_text(f"ℹ️ ID `{target}` tidak ditemukan dalam daftar Banned.")

    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ **Format Salah!**\nGunakan: `/unban ID_USER`")
    except Exception as e:
        await update.message.reply_text(f"❌ **Error Database:** {e}")

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    
    global DAFTAR_ADMIN
    try:
        if not context.args:
            return await update.message.reply_text("Format: /add_admin [ID]")

        new_id = int(context.args[0])
        
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT IGNORE INTO bot_admins (user_id) VALUES (%s)", (new_id,))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

        if new_id not in DAFTAR_ADMIN:
            DAFTAR_ADMIN.append(new_id)
            await update.message.reply_text(f"✅ `{new_id}` berhasil disimpan sebagai Admin.")
        else:
            await update.message.reply_text(f"ℹ️ `{new_id}` sudah menjadi Admin.")

    except ValueError:
        await update.message.reply_text("❌ ID harus berupa angka.")
    except Exception as e:
        print(f"Error add_admin: {e}")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return 
    
    global DAFTAR_ADMIN

    try:
        if not context.args:
            return await update.message.reply_text("Format: `/remove_admin ID`", parse_mode='Markdown')

        target = int(context.args[0])

        if target == DAFTAR_ADMIN[0]:
            return await update.message.reply_text("❌ Owner tidak bisa dihapus.")

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_admins WHERE user_id = %s", (target,))
        conn.commit()
        db_deleted = cursor.rowcount 
        cursor.close()
        conn.close()

        if target in DAFTAR_ADMIN:
            DAFTAR_ADMIN.remove(target)
            await update.message.reply_text(f"✅ `{target}` berhasil dihapus dari Database & RAM.")
        elif db_deleted > 0:
            await update.message.reply_text(f"✅ `{target}` dihapus dari Database.")
        else:
            await update.message.reply_text("❌ ID tidak ditemukan.")

    except (ValueError, IndexError):
        await update.message.reply_text("Format: `/remove_admin ID`")
    except Exception as e:
        print(f"Error remove_admin: {e}")

async def edit_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in DAFTAR_ADMIN: return

    # Format: /edit [KEY] [ASPEK] [NILAI]
    if len(context.args) < 3:
        msg = (
            "🔍 **Cara Edit Tunggal:**\n"
            "`/edit ITEM_A jam 00:00-23:59` (Gunakan tanda - untuk start-end)\n"
            "`/edit ITEM_A harga 500000-1000000` (Gunakan tanda - untuk min-max)\n"
            "`/edit ITEM_A profit 0.15` (Gunakan titik untuk desimal)\n"
            "`/edit ITEM_A hold 3` (Dalam hari)\n"
            "`/edit ITEM_A poin 10` (Biaya klik hunt)"
        )
        return await update.message.reply_text(msg, parse_mode='Markdown')

    key = context.args[0].upper() 
    aspek = context.args[1].lower()
    nilai = context.args[2]

    if key not in items_config:
        return await update.message.reply_text("❌ Key Item tidak ditemukan.")

    conn = get_db()
    cursor = conn.cursor()
    
    try:
        if aspek == "jam":
            start, end = nilai.split("-")
            cursor.execute("UPDATE items_config_db SET start_time = %s, end_time = %s WHERE item_key = %s", (start, end, key))
            items_config[key]['start_time'] = start
            items_config[key]['end_time'] = end
            info = f"⏰ Jam diubah: {start} - {end}"

        elif aspek == "harga":
            v_min, v_max = nilai.split("-")
            cursor.execute("UPDATE items_config_db SET price_min = %s, price_max = %s WHERE item_key = %s", (int(v_min), int(v_max), key))
            items_config[key]['min'] = int(v_min)
            items_config[key]['max'] = int(v_max)
            info = f"💰 Range Harga diubah: Rp{int(v_min):,} - Rp{int(v_max):,}"

        elif aspek == "profit":
            cursor.execute("UPDATE items_config_db SET profit_percent = %s WHERE item_key = %s", (float(nilai), key))
            items_config[key]['profit_percent'] = float(nilai)
            info = f"📈 Profit diubah: {int(float(nilai)*100)}%"

        elif aspek == "hold":
            cursor.execute("UPDATE items_config_db SET hold_days = %s WHERE item_key = %s", (int(nilai), key))
            items_config[key]['hold_days'] = int(nilai)
            info = f"⏳ Hold diubah: {nilai} Hari"

        elif aspek == "poin":
            cursor.execute("UPDATE items_config_db SET point_now = %s WHERE item_key = %s", (int(nilai), key))
            items_config[key]['point_now'] = int(nilai)
            info = f"🎫 Biaya Hunt diubah: {nilai} Poin"

        else:
            return await update.message.reply_text("❌ Aspek tidak dikenali (Gunakan: jam, harga, profit, hold, poin).")

        conn.commit()
        await update.message.reply_text(f"✅ **{key} UPDATE BERHASIL**\n{info}")

    except Exception as e:
        await update.message.reply_text(f"❌ Format salah atau error: {e}")
    finally:
        cursor.close()
        conn.close()

async def list_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("🚫 Akses Ditolak. Khusus Owner.")
    
    msg = "👑 **DAFTAR HIERARKI ADMIN**\n━━━━━━━━━━━━━━━━━━\n"
    for i, admin_id in enumerate(DAFTAR_ADMIN):
        role = "OWNER" if i == 0 else "ADMIN"
        msg += f"{i+1}. `{admin_id}` — *{role}*\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def isi_poin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    try:
        target, jml = int(context.args[0]), int(context.args[1])
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET points = points + %s WHERE user_id = %s", (jml, target))
        conn.commit()
        
        add_log(target, "TOPUP_ADMIN", jml, f"Diisi oleh Admin {update.effective_user.id}")
        
        if cursor.rowcount > 0:
            await update.message.reply_text(f"✅ **BERHASIL!**\nID: `{target}`\nDitambah: {jml} Poin")
            try: await context.bot.send_message(target, f"🎁 **Poin Masuk!**\nServer berhasil menambahkan {jml} poin.")
            except: pass
        else:
            await update.message.reply_text("❌ ID tidak ditemukan di Database.")
        cursor.close()
        conn.close()
    except: await update.message.reply_text("Format: `/isi_poin ID JML`")

async def kirim_poin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in DAFTAR_ADMIN: return

    try:
        target = int(context.args[0])
        jml = int(context.args[1])
        if jml <= 0: return await update.message.reply_text("❌ Jumlah harus lebih dari 0.")
            
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT points FROM users WHERE user_id = %s", (uid,))
        admin_data = cursor.fetchone()
        
        if not admin_data or admin_data['points'] < jml:
            cursor.close()
            conn.close()
            return await update.message.reply_text(f"❌ Poin tidak cukup.")

        cursor.execute("UPDATE users SET points = points - %s WHERE user_id = %s", (jml, uid))
        cursor.execute("UPDATE users SET points = points + %s WHERE user_id = %s", (jml, target))
        conn.commit()

        if cursor.rowcount > 0:
            await update.message.reply_text(f"✅ Berhasil kirim {jml} poin ke `{target}`.")
            try:
                await context.bot.send_message(target, f"🎁 Poin ditambahkan {jml} oleh Admin.")
            except: pass
        else:
            await update.message.reply_text("❌ Gagal. Target tidak ditemukan.")

        cursor.close()
        conn.close()
    except:
        await update.message.reply_text("Format: `/kirim_poin ID JML`")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_rate_limited(update): return
    uid = update.effective_user.id
    
    msg = (
        "<b>📖 PANDUAN PLAYER</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "1. <b>Daftar</b>: Ketik /start & isi rekening di /set_rekening.\n"
        "2. <b>Hunt</b>: Ketik /hunt untuk berburu aset Digital.\n"
        "3. <b>Market</b>: Ketik /market untuk pantau market.\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎮 <code>/start</code> - Daftar Akun\n"
        "💳 <code>/set_rekening</code> - Atur Bank\n"
        "🎯 <code>/hunt</code> - Cari Item\n"
        "🎟 <code>/claim_res</code> - Klaim Item Reservasi\n"
        "🕒 <code>/market</code> - Jadwal & Harga\n"
        "👤 <code>/info</code> - Poin & Aset\n"        
        "🔗 <code>/myref</code> - Link Referral Anda\n"
        f"👥 Ajak teman & dapatkan <b>{REF_BONUS_POIN} Poin</b>!\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💰 <b>CARA TOP UP POIN:</b>\n"
        f"1. Transfer ke: <code>{REK_ADMIN_UTAMA}</code>\n"
        f"2. Kirim bukti transfer ke: {HUBUNGI_ADMIN}\n"
        "3. Sertakan ID Player kamu (Cek di /info)\n"
    )

    if uid in DAFTAR_ADMIN:
        msg += (
            "\n🛠 <b>MENU ADMIN</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "💸 <code>/kirim_poin ID JML</code> - Kirim Poin\n"
            "🚫 <code>/ban ID</code> - Blokir Permanen\n"
            "🔓 <code>/unban ID</code> - Buka Blokir\n"
            "🔄 <code>/reset_rek ID</code> - Reset Rekening\n"
            "🔍 <code>/cek_pembeli ID</code> - Cek Detail Sengketa\n"
        )

    if uid == DAFTAR_ADMIN[0]:
        msg += (
            "\n👑 <b>MENU OWNER</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📢 <code>/bc pesan</code> - Broadcast\n"
            "💳 <code>/set_rek_admin</code> - Ubah Rek Topup\n"
            "⚖️ <code>/konfirmasi_paksa</code> - Sengketa ⚠️\n"
            "💰 <code>/isi_poin</code> - Tambah Poin\n"
            "📊 <code>/cek_antrean</code> - Pantau Antrean\n"
            "⚙️ <code>/edit</code> - Update Market\n"
            "🔄 <code>/reload</code> - Update Data (RAM & DB)\n"
            "♻️ <code>/restart_total</code> - Reboot Script Bot\n"
            "👤 <code>/add_admin ID</code> - Tambah Admin Baru\n"
            "🗑 <code>/remove_admin ID</code> - Hapus Admin\n"
        )

    msg += f"\n\n🆘 <b>Bantuan:</b> {HUBUNGI_ADMIN}"
    await update.message.reply_text(msg, parse_mode='HTML')

async def set_rekening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    raw = " ".join(context.args).strip().upper()

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT rekening FROM users WHERE user_id = %s", (uid,))
    user = cursor.fetchone()
    
    if not user:
        return await update.message.reply_text("⛔ Ketik /start dahulu.")

    if user['rekening'] != "Belum diatur":
        return await update.message.reply_text("🚫 Rekening terkunci. Hubungi Admin.")

    if not raw or " A/N " not in raw:
        return await update.message.reply_text("Format: `/set_rekening BANK NOREK A/N NAMA`", parse_mode='Markdown')

    blacklist = ["DANA", "OVO", "GOPAY", "LINKAJA", "SHOPEE", "SPAY", "SAKUKU", "I-SAKU", "DOKU"]
    if any(wallet in raw for wallet in blacklist):
        return await update.message.reply_text("🚫 **E-Wallet Ditolak!** Gunakan rekening Bank resmi untuk keamanan P2P.")

    raw_clean = raw.replace(" ", "").replace("-", "").replace("+", "")
    if any(raw_clean.find(prefix) != -1 for prefix in ["08", "628"]):
        numeric_part = ''.join(filter(str.isdigit, raw_clean))
        if len(numeric_part) >= 10:
            return await update.message.reply_text("🚫 **Nomor HP Ditolak!** Gunakan nomor rekening Bank.")

    cursor.execute("SELECT user_id FROM users WHERE rekening = %s", (raw,))
    if cursor.fetchone():
        return await update.message.reply_text("❌ Rekening ini sudah terdaftar di sistem!")

    cursor.execute("UPDATE users SET rekening = %s WHERE user_id = %s", (raw, uid))
    conn.commit()
    
    cursor.close()
    conn.close()
    
    await update.message.reply_text(f"✅ **Rekening disimpan:**\n`{raw}`", parse_mode='Markdown')

async def reset_rekening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in DAFTAR_ADMIN: return
    
    try:
        target_id = int(context.args[0])
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT is_hunting FROM users WHERE user_id = %s", (target_id,))
        user_db = cursor.fetchone()
        
        if user_db:
            if user_db['is_hunting']:
                cursor.close()
                conn.close()
                return await update.message.reply_text("❌ User sedang dalam transaksi aktif! Selesaikan atau tunggu ban dahulu.")

            cursor.execute("UPDATE users SET rekening = 'Belum diatur' WHERE user_id = %s", (target_id,))
            conn.commit()
            
            for item_key in items_config:
                for owner in items_config[item_key]['owners_queue']:
                    if owner['id'] == target_id:
                        owner['rek'] = 'Belum diatur'
            
            cursor.close()
            conn.close()
            
            await update.message.reply_text(f"✅ Rekening ID `{target_id}` telah di-reset.")
            
            try:
                await context.bot.send_message(target_id, "⚠️ **PEMBERITAHUAN ADMIN**\n\nRekening Anda telah di-reset. Silakan atur ulang dengan `/set_rekening`.", parse_mode='Markdown')
            except: pass
        else:
            cursor.close()
            conn.close()
            await update.message.reply_text("❌ ID tidak ditemukan di database.")
            
    except (IndexError, ValueError):
        await update.message.reply_text("Format: `/reset_rek [ID_USER]`")
    except Exception as e:
        await update.message.reply_text(f"❌ Terjadi kesalahan: {e}")

async def konfirmasi_paksa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # --- PROTEKSI ADMIN ---
    if update.effective_user.id != DAFTAR_ADMIN[0]:
        return await update.message.reply_text("🚫 Perintah ini hanya untuk Owner Utama.")

    try:
        # Mengambil ID Pembeli dari command, misal: /konfirmasi_paksa 123456
        bid = int(context.args[0]) 
        
        tr = active_trades.get(bid)
        
        if not tr:
            return await update.message.reply_text("❌ Data transaksi tidak ditemukan. Transaksi mungkin sudah selesai atau expired.")

        sid = tr['seller_id'] # ID Penjual (yang bermasalah)
        item_key = tr['item_key']
        item = items_config[item_key]
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 1. AMBIL DATA REKENING PEMBELI
        cursor.execute("SELECT rekening FROM users WHERE user_id = %s", (bid,))
        res_p = cursor.fetchone()
        rek_pembeli = res_p['rekening'] if res_p else "Belum diatur"

        cursor.execute(
            "INSERT INTO user_assets (user_id, item_key, buy_price, buy_time, is_selling) VALUES (%s, %s, %s, %s, 0)",
            (bid, item_key, tr['harga'], datetime.now(tz_jkt).replace(tzinfo=None))
        )
        asset_id_unik = cursor.lastrowid 

        item['owners_queue'].append({
            'asset_id': asset_id_unik, # ID Unik dari SQL
            'id': bid, 
            'rek': rek_pembeli, 
            'buy_time': datetime.now(tz_jkt).replace(tzinfo=None)
        })
        
        if not tr.get('is_new_stock'):
            for p in item['owners_queue'][:]:
                if p['id'] == sid:
                    item['owners_queue'].remove(p)
                    break
        
        cursor.execute("UPDATE users SET is_hunting = 0 WHERE user_id = %s", (bid,))
        conn.commit()
        
        del active_trades[bid]
        item['is_locked'] = False

        await update.message.reply_text(
            f"✅ **EKSEKUSI TRANSAKSI BERHASIL**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📦 Item: `{item['name']}`\n"
            f"🆔 Asset ID: `{asset_id_unik}`\n"
            f"👤 Pembeli: `{bid}` (Aset Masuk)\n"
            f"👤 Penjual: `{sid}` (Aset Ditarik)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Status: **Sengketa Selesai.**"
        )
        
        try:
            await context.bot.send_message(
                bid, 
                f"🎉 **PEMBERITAHUAN ADMIN**\n\n"
                f"Aset `{item['name']}` dengan **ID: {asset_id_unik}** telah dipindahkan ke akun Anda secara manual oleh Admin.\n\n"
                f"Sekarang Anda sudah bisa melakukan `/hunt` kembali."
            )
        except: pass

        cursor.close()
        conn.close()

    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ **Format Salah!**\nGunakan: `/konfirmasi_paksa ID_PEMBELI`")
    except Exception as e:
        await update.message.reply_text(f"❌ **Terjadi Kesalahan:** {e}")

async def cek_antrean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in DAFTAR_ADMIN: return
    
    msg = "📊 **STATUS ANTREAN MARKET**\n━━━━━━━━━━━━━━━━━━\n\n"
    for k, v in items_config.items():
        queue = v['owners_queue']
        total_owner = len(queue)
        
        top_owners = ", ".join([str(p['id']) for p in queue[:3]])
        detail = f"({top_owners}...)" if total_owner > 3 else f"({top_owners})" if total_owner > 0 else "(Kosong)"
        
        msg += (
            f"📦 *{v['name']}*\n"
            f"└ Total: {total_owner} Pemilik\n"
            f"└ Antrean: `{detail}`\n\n"
        )
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def hard_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in DAFTAR_ADMIN:
        return await update.message.reply_text("⛔ Akses ditolak.")
    
    await update.message.reply_text("♻️ **Sistem Rebooting...**\nBot sedang dimuat ulang.")
    
    try:
        conn = get_db()
        conn.close()
    except:
        pass

    print("\n[!] RESTART COMMAND RECEIVED - SHUTTING DOWN...")

    context.application.stop_running()

    os._exit(0)

async def reload_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in DAFTAR_ADMIN:
        return await update.message.reply_text("⛔ Akses ditolak.")

    try:
        await sync_assets_to_ram()
        print(f"🔄 LOG: Sinkronisasi ulang oleh Admin {uid}")
        await update.message.reply_text("✅ **Data Disinkronkan!**\nRAM telah diperbarui dengan data terbaru dari database.")
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal sinkronisasi: {e}")

async def cek_pembeli(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in DAFTAR_ADMIN:
        return 

    if not context.args:
        return await update.message.reply_text("Format: /cek_pembeli [ID_PEMBELI]")

    try:
        bid = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ ID Pembeli harus berupa angka.")

    tr = active_trades.get(bid)

    if not tr:
        return await update.message.reply_text("❌ Tidak ada transaksi aktif untuk ID tersebut di RAM.")

    msg = (
        f"🔍 **DETAIL TRANSAKSI AKTIF**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 Pembeli: `{bid}`\n"
        f"👤 Penjual: `{tr['seller_id']}`\n"
        f"📦 Item: `{tr['item_key']}`\n"
        f"💰 Nominal: **Rp{tr['harga']:,}**\n"
        f"📸 Bukti Terkirim: {'✅' if tr.get('bukti_sent') else '❌'}\n"
    )
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in DAFTAR_ADMIN:
        return

    if not context.args:
        return await update.message.reply_text("Format: /ban [ID_USER]")

    target_id = int(context.args[0])
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = %s", (target_id,))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(f"🚫 User `{target_id}` telah diblokir permanen.")

async def set_rek_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Khusus Owner (Admin Utama / DAFTAR_ADMIN[0])
    if update.effective_user.id != DAFTAR_ADMIN[0]:
        return # Bisukan jika bukan owner utama

    if not context.args:
        return await update.message.reply_text(
            "Format: `/set_rek_admin NAMA_BANK - NO_REK - A/N`", 
            parse_mode='Markdown'
        )

    # Menggabungkan semua argumen menjadi satu string rekening baru
    rekening_baru = " ".join(context.args)
    
    # Update variabel global di RAM
    global REK_ADMIN_UTAMA
    REK_ADMIN_UTAMA = rekening_baru

    await update.message.reply_text(
        f"✅ **REKENING ADMIN BERHASIL DIUBAH!**\n\n"
        f"Sekarang menjadi:\n`{REK_ADMIN_UTAMA}`\n\n"
        f"⚠️ *Perubahan ini hanya berlaku di RAM. Jika bot restart, akan kembali ke default script.*",
        parse_mode='Markdown'
    )

# ==========================================
# 4. CORE LOGIC (HUNT & P2P)
# ==========================================

async def hunt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_rate_limited(update): return
    if not await check_user(update): return
    uid = update.effective_user.id
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT is_hunting, rekening FROM users WHERE user_id = %s", (uid,))
    user = cursor.fetchone()

    if user['is_hunting']:
        cursor.close()
        conn.close()
        return await update.message.reply_text("❌ Selesaikan transaksi yang sedang aktif!")
    
    if user['rekening'] == "Belum diatur":
        cursor.close()
        conn.close()
        return await update.message.reply_text("⚠️ Atur rekening dulu dengan /set_rekening.")

    now = datetime.now(tz_jkt).replace(tzinfo=None)
    now_str = now.strftime("%H:%M")
    active_key = None
    
    for k, v in items_config.items():
        if v['start_time'] <= now_str <= v['end_time']:
            active_key = k
            break

    if not active_key:
        cursor.close()
        conn.close()
        return await update.message.reply_text("🕒 Market Tutup, check /market.")
    
    item = items_config[active_key]
    profit_display = int(item['profit_percent'] * 100)
    
    url_webapp = "https://ozaidotexe.github.io/market-terminal/" 
    keyboard = [[
        KeyboardButton(
            text="🎮 MASUK ARENA HUNTING", 
            web_app=WebAppInfo(url=url_webapp)
        )
    ]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    pesan_detail = (
        f"🚀 **MARKET {item['name']} TERBUKA!**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 **Range:** Rp{item['min']:,} - Rp{item['max']:,}\n"
        f"📈 **Profit:** {profit_display}% (+ Rp{(int(item['min']*item['profit_percent'])):,})\n"
        f"⏳ **Hold:** {item['hold_days']} Hari\n"
        f"🎫 **Poin Hunt:** {item['point_now']} Poin\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Klik tombol di bawah untuk masuk ke Arena Hunting!"
    )

    await update.message.reply_text(
        pesan_detail, 
        reply_markup=reply_markup, 
        parse_mode='Markdown'
    )
    
    cursor.close()
    conn.close()

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    web_data = update.effective_message.web_app_data.data
    uid = update.effective_user.id
    
    if web_data == "proses_hunt": 
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE user_id = %s", (uid,))
        user = cursor.fetchone()

        if not user:
            cursor.close()
            conn.close()
            return await update.message.reply_text("❌ Data user tidak ditemukan. Klik /start dulu.")

        now = datetime.now(tz_jkt).replace(tzinfo=None)
        now_to_db = now.replace(tzinfo=None)

        COOLDOWN_SECONDS = 20  
        if user['last_hunt_time'] is not None:
            last_hunt = user['last_hunt_time'].replace(tzinfo=None)
            selisih = (now_to_db - last_hunt).total_seconds()
            if selisih < COOLDOWN_SECONDS:
                cursor.close()
                conn.close()
                return await update.message.reply_text(f"⏳ Cooldown {int(COOLDOWN_SECONDS - selisih)} detik.")

        now_str = now.strftime("%H:%M")
        active_key = next((k for k, v in items_config.items() if v['start_time'] <= now_str <= v['end_time']), None)

        if not active_key:
            cursor.close()
            conn.close()
            return await update.message.reply_text("🕒 Market sudah tutup, Silakan cek /market.")

        item = items_config[active_key]
        cost = 0 if user['res_active'] == active_key else item['point_now']
        label_status = "Gratis (Reservasi)" if cost == 0 else f"Potong {cost} Poin"

        if user['points'] < cost:
            cursor.close()
            conn.close()
            return await update.message.reply_text(f"❌ Poin kurang.")

        
        cursor.execute("UPDATE users SET last_hunt_time = %s WHERE user_id = %s", (now_to_db, uid))
        conn.commit()

        if random.random() < 0.5:
            penjual = next((p for p in item['owners_queue'] if p['id'] != uid), None)
            
            if penjual and item.get('is_locked'):
                cursor.close()
                conn.close()
                return await update.message.reply_text("⏳ Aset sedang diproses pembeli lain. Coba beberapa saat lagi!")

            if penjual:
                new_price = int(penjual.get('buy_price', item['current_price']) * (1 + item['profit_percent']))
                item['is_locked'] = True
            else:
                new_price = random.randint(item['min'], int((item['min']+item['max'])/2))
                
                # --- PENAMBAHAN NOTIFIKASI ADMIN UTAMA ---
                try:
                    await context.bot.send_message(
                        chat_id=DAFTAR_ADMIN[0],
                        text=(
                            f"🚨 **STOK ADMIN TERJUAL (PENDING)**\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"📦 Item: `{active_key}`\n"
                            f"👤 Pembeli: `{uid}`\n"
                            f"💰 Nominal: `Rp{new_price:,}`\n"
                            f"⏳ Status: Menunggu Bukti Transfer dari pembeli."
                        ),
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    print(f"Gagal kirim notif ke Admin Utama: {e}")
                # -----------------------------------------------------------------
            
            res_sql = ", res_active = NULL" if cost == 0 else ""
            cursor.execute(f"UPDATE users SET points = points - %s, is_hunting = 1 {res_sql} WHERE user_id = %s", (cost, uid))
            conn.commit()
            
            add_log(uid, "HUNT_SUCCESS", -cost, f"Berhasil hunting item {active_key}")
            
            active_trades[uid] = {
                'harga': new_price, 'item_key': active_key, 
                'seller_id': penjual['id'] if penjual else DAFTAR_ADMIN[0], 
                'is_new_stock': penjual is None
            }
            
            context.job_queue.run_once(job_ban_pembeli, 1800, data={'uid': uid, 'item_key': active_key}, name=f"ban_buyer_{uid}")
            dest_rek = penjual['rek'] if penjual else REK_ADMIN_UTAMA
            
            await update.message.reply_text(
                f"🎯 **HUNT BERHASIL!**\n"
                f"💰 Nominal: **Rp{new_price:,}**\n"
                f"💳 Rekening: `{dest_rek}`\n\n"
                f"⚠️ Kirim Bukti Transfer dalam 30 menit atau akun diblokir!"
            )
        else:
            res_sql = ", res_active = NULL" if cost == 0 else ""
            cursor.execute(f"UPDATE users SET points = points - %s {res_sql} WHERE user_id = %s", (cost, uid))
            conn.commit()
            await update.message.reply_text(f"💨 Gagal mendapatk an item! {label_status}")
            add_log(uid, "HUNT_FAIL", -cost, f"Gagal hunting item {active_key}")
            
        cursor.close()
        conn.close()
    else:
        print(f"DEBUG: Webapp mengirim data tidak dikenal: {web_data}")             

async def claim_reservation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    now = datetime.now(tz_jkt).replace(tzinfo=None)
    now_str = now.strftime("%H:%M")
    
    target_item = None
    for k, v in items_config.items():
        start_dt = datetime.strptime(v['start_time'], "%H:%M")
        claim_window_start = (start_dt - timedelta(minutes=5)).strftime("%H:%M")
        if claim_window_start <= now_str < v['start_time']:
            target_item = k
            break
            
    if not target_item:
        return await update.message.reply_text("❌ Belum waktunya reservasi. Hanya bisa 5 menit sebelum market buka.")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT res_active, points FROM users WHERE user_id = %s", (uid,))
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return await update.message.reply_text("❌ Data tidak ditemukan.")

    if user['res_active']:
        cursor.close()
        conn.close()
        return await update.message.reply_text(f"⚠️ Kamu sudah punya reservasi aktif.")

    res_cost = items_config[target_item].get('point_res', 0) 
    if user['points'] < res_cost:
        cursor.close()
        conn.close()
        return await update.message.reply_text(f"❌ Poin kurang. Butuh {res_cost} Poin untuk reservasi.")

    cursor.execute("UPDATE users SET res_active = %s, points = points - %s WHERE user_id = %s", (target_item, res_cost, uid))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(
        f"✅ **RESERVASI BERHASIL!**\n📦 Item: {target_item}\n💰 Biaya Res: {res_cost} Poin\n"
        f"🎫 Status: Hunt jam {items_config[target_item]['start_time']} nanti GRATIS."
    )
    
async def job_auto_growth_assets(context: ContextTypes.DEFAULT_TYPE):
    """Mengecek aset yang sudah lewat masa hold dan menaikkan harganya secara otomatis (DB & RAM Sinkron)."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    now = datetime.now(tz_jkt).replace(tzinfo=None)
    try:
        cursor.execute("SELECT * FROM user_assets WHERE is_selling = 0")
        assets = cursor.fetchall()
        
        for asset in assets:
            item_key = asset['item_key']
            if item_key not in items_config: 
                continue
                
            conf = items_config[item_key]
            days_held = (now - asset['buy_time']).days
            
            if days_held >= conf['hold_days']:
                bonus_profit = int(asset['buy_price'] * 0.01) #Settingan 1% aset tumbuh
                new_price = asset['buy_price'] + bonus_profit
                
                cursor.execute("""
                    UPDATE user_assets 
                    SET buy_price = %s, buy_time = %s 
                    WHERE id = %s
                """, (new_price, now, asset['id']))
                
                for owner in conf['owners_queue']:
                    if owner.get('asset_id') == asset['id']:
                        owner['buy_price'] = new_price 
                        owner['buy_time'] = now
                
                print(f"📈 [AUTO-GROWTH] Aset ID {asset['id']} ({conf['name']}) naik menjadi Rp{new_price:,}")

        conn.commit()
    except Exception as e: 
        print(f"❌ Error Auto-Growth: {e}")
    finally: 
        cursor.close()
        conn.close()

async def handle_bukti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    if uid not in active_trades: 
        return
    
    tr = active_trades[uid]
    
    if tr.get('bukti_sent'):
        return

    photo_id = update.message.photo[-1].file_unique_id
    
    if photo_id in used_photos:
        return await update.message.reply_text(
            "🚫 **FOTO DUPLIKAT!**\n"
            "Bukti transfer ini sudah pernah digunakan. "
            "Jangan mencoba menipu sistem atau hubungi Admin jika ada kendala."
        )

    tr['bukti_sent'] = True
    used_photos.add(photo_id)
    
    for j in context.job_queue.get_jobs_by_name(f"ban_buyer_{uid}"): 
        j.schedule_removal()
    
    context.job_queue.run_once(
        job_ban_penjual, 
        2700, 
        data={'sid': tr['seller_id'], 'bid': uid}, 
        name=f"ban_seller_{uid}"
    )
    
    kb = [[InlineKeyboardButton("✅ TERIMA DANA", callback_data=f"app_{uid}")]]
    
    try:
        await context.bot.send_photo(
            chat_id=tr['seller_id'], 
            photo=update.message.photo[-1].file_id, 
            caption=(
                f"📩 **BUKTI MASUK**\n"
                f"👤 ID Pembeli: `{uid}`\n"
                f"💰 Nominal: Rp{tr['harga']:,}\n"
                f"⏰ Batas: 45 Menit"
            ), 
            reply_markup=InlineKeyboardMarkup(kb), 
            parse_mode='Markdown'
        )
        await update.message.reply_text("✅ Bukti terkirim. Menunggu konfirmasi penjual.")
    except Exception as e:
        tr['bukti_sent'] = False
        if photo_id in used_photos:
            used_photos.remove(photo_id)
        
        print(f"Error send_photo: {e}")
        await update.message.reply_text("❌ Gagal mengirim bukti ke penjual. Silakan coba lagi.")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    if not q.data.startswith("app_"):
        return

    bid = int(q.data.split("_")[1])
    tr = active_trades.get(bid)
    if not tr: 
        return await q.edit_message_caption("❌ Transaksi kadaluwarsa atau sudah diproses.")
    
    sid = tr['seller_id']
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        conn.start_transaction()
        

        cursor.execute("SELECT referred_by, ref_reward_claimed FROM users WHERE user_id = %s FOR UPDATE", (sid,))
        penjual_db = cursor.fetchone()
        
        pengajak_id = None 
        if penjual_db and penjual_db['referred_by'] and not penjual_db['ref_reward_claimed']:
            pengajak_id = penjual_db['referred_by']
            cursor.execute("UPDATE users SET points = points + %s WHERE user_id = %s", (REF_BONUS_POIN, pengajak_id))
            cursor.execute("UPDATE users SET ref_reward_claimed = TRUE WHERE user_id = %s", (sid,))
            add_log(pengajak_id, "BONUS_REF", REF_BONUS_POIN, f"Bonus dari penjualan pertama user {sid}")

        cursor.execute("SELECT rekening FROM users WHERE user_id = %s", (bid,))
        pembeli_db = cursor.fetchone()
        rek_pembeli = pembeli_db['rekening'] if pembeli_db else "Belum diatur"
        
        cursor.execute(
            "INSERT INTO user_assets (user_id, item_key, buy_price, buy_time, is_selling) VALUES (%s, %s, %s, %s, 0)",
            (bid, tr['item_key'], tr['harga'], datetime.now(tz_jkt).replace(tzinfo=None))
        )
        asset_id_unik = cursor.lastrowid
        
        add_log(bid, "BUY_ITEM", 0, f"Sukses beli item ID {asset_id_unik}")
        add_log(sid, "SELL_ITEM", 0, f"Sukses jual item ID {asset_id_unik}")
        
        cursor.execute("UPDATE users SET is_hunting = FALSE WHERE user_id = %s", (bid,))

        conn.commit()
        
        for j in context.job_queue.get_jobs_by_name(f"ban_seller_{bid}"): 
            j.schedule_removal()

        item = items_config[tr['item_key']]
        item['current_price'] = tr['harga']
        
        item['owners_queue'].append({
            'asset_id': asset_id_unik, 
            'id': bid, 
            'rek': rek_pembeli, 
            'buy_time': datetime.now(tz_jkt).replace(tzinfo=None)
        })

        if not tr['is_new_stock']:
            item['owners_queue'] = [p for p in item['owners_queue'] if p['id'] != sid]

        if pengajak_id:
            try:
                await context.bot.send_message(
                    chat_id=pengajak_id,
                    text=f"🎁 **Bonus Referral Cair!**\n\nTeman yang Anda ajak (`{sid}`) sukses menjual aset.\n💰 **+{REF_BONUS_POIN} Poin** ditambahkan!",
                    parse_mode='Markdown'
                )
            except: pass

        item['is_locked'] = False
        del active_trades[bid]
        
        await q.edit_message_caption("✅ TRANSAKSI SELESAI.")
        await context.bot.send_message(bid, "🎉 Selamat! Aset sudah masuk ke akun Anda.")

    except Exception as e:
        if conn.is_connected():
            conn.rollback()
        print(f"❌ ERROR TRANSACTION: {e}")
        await q.message.reply_text("⚠️ Gagal memproses transaksi. Silakan hubungi admin.")

    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

# ==========================================
# 5. REGISTRASI & INFO
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (uid,))
    if not cursor.fetchone():
        ref_id = None
        
        if context.args and context.args[0].isdigit():
            target_ref = int(context.args[0])
            
            if target_ref != uid: 
                ref_id = target_ref

        cursor.execute(
            "INSERT INTO users (user_id, points, rekening, referred_by) VALUES (%s, 100, 'Belum diatur', %s)",
            (uid, ref_id)
        )
        conn.commit()
        
        add_log(uid, "REGISTER", 100, "Bonus pendaftaran user baru")
        
    cursor.close()
    conn.close()
    await update.message.reply_text("✅ Bot Aktif. Gunakan /help.")

async def my_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_rate_limited(update): return
    uid = update.effective_user.id
    bot_info = await context.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={uid}"
    
    # Tulis pesan sesukamu dengan spasi normal
    pesan_ajakan = "Ayo gabung di P2P Bug Box Hunting ini! Dapatkan cuan bersama."
    
    pesan_encoded = urllib.parse.quote(pesan_ajakan)
    
    share_url = f"https://t.me/share/url?url={link}&text={pesan_encoded}"
    
    keyboard = [[InlineKeyboardButton("🚀 BAGIKAN KE TEMAN", url=share_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🔗 **LINK REFERRAL ANDA**\n\n"
        f"<code>{link}</code>\n\n"
        f"Dapatkan **{REF_BONUS_POIN} Poin** setelah teman yang Anda ajak berhasil menjual aset pertamanya!",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def info_aset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_rate_limited(update): return
    if not await check_user(update):
        return
        
    uid = update.effective_user.id
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT points, res_active FROM users WHERE user_id = %s", (uid,))
        user_sql = cursor.fetchone()
    except Exception as e:
        print(f"Error SQL Info Aset: {e}")
        return await update.message.reply_text("❌ Gagal mengambil data profil.")
    finally:
        cursor.close()
        conn.close()

    if not user_sql:
        return await update.message.reply_text("❌ Data user tidak ditemukan,Silakan cek /help.")

    aset = []
    total_nilai_aset = 0
    
    for k, v in items_config.items():
        user_assets_in_item = [p for p in v['owners_queue'] if p['id'] == uid]
        count = len(user_assets_in_item)
        
        if count > 0:
            sub_total = sum(p.get('buy_price', v['current_price']) for p in user_assets_in_item)
            total_nilai_aset += sub_total
            
            aset.append(f"• {v['name']} ({count}x) — Rp{sub_total:,}")
            
    res_key = user_sql['res_active']
    if res_key and res_key in items_config:
        status_res = f"🎫 Tiket Res: {items_config[res_key]['name']}"
    else:
        status_res = "🎫 Tiket Res: Kosong"
        
    pesan = (
        f"👤 **PROFIL PLAYER**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ID: `{uid}`\n"
        f"🪙 Poin: **{user_sql['points']:,}**\n"
        f"💰 Est. Nilai Aset: **Rp{total_nilai_aset:,}**\n" # Baris baru
        f"{status_res}\n\n"
        f"📦 **KOLEKSI ASET:**\n"
        + ("\n".join(aset) if aset else "_Belum memiliki aset_")
        + "\n━━━━━━━━━━━━━━━━━━"
    )
    
    await update.message.reply_text(pesan, parse_mode='Markdown')

async def info_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_rate_limited(update): return
    
    url_katalog = "https://www.upload.ee/image/19231502/BugBoxHunter.png" 
    
    msg = "🕒 **JADWAL MARKET & PROFIT**\n━━━━━━━━━━━━━━━━━━\n\n"
    for k, v in items_config.items():
        p_persen = int(v['profit_percent'] * 100)
        
        p_res = v.get('point_res', 0)
        p_now = v.get('point_now', 0)
        
        msg += (
            f"📦 *{v['name']}*\n"
            f"💰 Rp{v['min']:,} - Rp{v['max']:,}\n"
            f"📈 Profit: {p_persen}% | ⏳ {v['hold_days']} Hari\n"
            f"🎫 Poin: {p_res}/{p_now} [Res/Now]\n"  # Baris baru yang ditambahkan
            f"⏰ {v['start_time']} - {v['end_time']}\n\n"
        )

    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=url_katalog,
            caption=msg,
            parse_mode='Markdown'
        )
    except:
        await update.message.reply_text(msg, parse_mode='Markdown')

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_rate_limited(update): return
    if not await check_user(update): return
    uid = update.effective_user.id
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        query = """
            SELECT ua.user_id, SUM(ua.buy_price) as total_nilai, COUNT(ua.id) as total_aset
            FROM user_assets ua
            WHERE ua.is_selling = 0
            GROUP BY ua.user_id
            ORDER BY total_nilai DESC
            LIMIT 5
        """
        cursor.execute(query)
        top_players = cursor.fetchall()

        query_posisi = """
            SELECT total_nilai, rank_pos FROM (
                SELECT user_id, SUM(buy_price) as total_nilai, 
                RANK() OVER (ORDER BY SUM(buy_price) DESC) as rank_pos
                FROM user_assets WHERE is_selling = 0
                GROUP BY user_id
            ) as ranking WHERE user_id = %s
        """
        cursor.execute(query_posisi, (uid,))
        user_rank = cursor.fetchone()

        pesan = "🏆 **LEADERBOARD TOP HUNTER** 🏆\n"
        pesan += "━━━━━━━━━━━━━━━━━━\n"
        
        medals = ["🥇", "🥈", "🥉", "👤", "👤"]
        
        if not top_players:
            pesan += "_Belum ada data aset._\n"
        else:
            for i, p in enumerate(top_players):
                icon = medals[i]
                
                uid_str = str(p['user_id'])
                uid_sensor = "****" + uid_str[-4:] 
                
                pesan += f"{icon} `{uid_sensor}` — **Rp{p['total_nilai']:,}** ({p['total_aset']} Aset)\n"
        
        pesan += "━━━━━━━━━━━━━━━━━━\n"
        
        if user_rank:
            pesan += f"Posisi Anda: **#{user_rank['rank_pos']}** (Rp{user_rank['total_nilai']:,})"
        else:
            pesan += "Posisi Anda: **#0** (Rp0)"

        await update.message.reply_text(pesan, parse_mode='Markdown')

    except Exception as e:
        print(f"Error Leaderboard: {e}")
        await update.message.reply_text("❌ Gagal memuat leaderboard.")
    finally:
        cursor.close()
        conn.close()

# ==========================================
# RUN BOT
# ==========================================

async def sync_assets_to_ram():
    print("🔄 Menyinkronkan aset dari Database ke RAM...")
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT ua.item_key, ua.id as asset_id, ua.user_id, ua.buy_time, u.rekening 
            FROM user_assets ua 
            JOIN users u ON ua.user_id = u.user_id 
            WHERE ua.is_selling = 0
            ORDER BY ua.buy_time ASC
        """
        cursor.execute(query)
        rows = cursor.fetchall()

        for k in items_config:
            items_config[k]['owners_queue'] = []

        for row in rows:
            item_key = row['item_key']
            if item_key in items_config:
                items_config[item_key]['owners_queue'].append({
                    'asset_id': row['asset_id'],
                    'id': row['user_id'],
                    'rek': row['rekening'],
                    'buy_time': row['buy_time']
                })
        
        print(f"✅ Sinkronisasi selesai. {len(rows)} aset dimuat.")
    except Exception as e:
        print(f"❌ ERROR RAM Sync: {e}")
    finally:
        if conn:
            cursor.close()
            conn.close()

# =====================================================================
# KODE SERVER API
# =====================================================================
app_api = Flask(__name__)
CORS(app_api)

@app_api.route('/api/user/info', methods=['GET'])
def get_user_info():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({"status": "error", "message": "User ID tidak disertakan"}), 400
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT points, status_hunt, rekening, reservation FROM users WHERE user_id = %s", (user_id,))
        user_data = cursor.fetchone()
        
        if not user_data:
            return jsonify({"status": "error", "message": "User tidak ditemukan. Silakan tekan /start di Bot Telegram terlebih dahulu!"}), 404
        
        cursor.execute("SELECT id, item_key, buy_price FROM user_assets WHERE user_id = %s", (user_id,))
        assets = cursor.fetchall()
        
        total_estimasi = sum(asset['buy_price'] for asset in assets)
        
        return jsonify({
            "status": "success",
            "data": {
                "user_id": user_id,
                "points": user_data['points'],
                "status_hunt": user_data['status_hunt'],
                "rekening": user_data['rekening'] if user_data['rekening'] else "Belum di-set",
                "reservation": user_data['reservation'] if user_data['reservation'] else "Kosong",
                "estimasi_aset": total_estimasi,
                "assets": assets
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app_api.route('/api/user/hunt', methods=['POST'])
def process_user_hunt():
    data = request.json
    user_id = data.get('user_id')
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT reason FROM banned WHERE user_id = %s", (user_id,))
        is_banned = cursor.fetchone()
        if is_banned:
            return jsonify({"status": "error", "message": f"Anda dibanned! Alasan: {is_banned[0]}"}), 403
            
        return jsonify({"status": "success", "message": "🎯 Hunting Berhasil! Aksi Anda tercatat di server."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

def run_flask():
    app_api.run(host='localhost', port=5001, debug=False, use_reloader=False)

# =====================================================================
# TOMBOL START UTAMA JALANNYA BOT & API
# =====================================================================
if __name__ == '__main__':
    init_db()
    
    try:
        asyncio.run(sync_assets_to_ram())
    except Exception as ex:
        print(f"Gagal sinkronisasi awal: {ex}")
        
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    app = ApplicationBuilder().token(TOKEN_BOT).build()
    
    # Daftarkan Semua Perintah (Handlers) Utama
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("market", info_market))
    app.add_handler(CommandHandler("info", info_aset))
    app.add_handler(CommandHandler("myref", my_referral))
    app.add_handler(CommandHandler("bc", broadcast))
    app.add_handler(CommandHandler("edit", edit_item))
    app.add_handler(CommandHandler("hunt", hunt))
    app.add_handler(CommandHandler("top", leaderboard))
    app.add_handler(CommandHandler("reload", reload_data))
    app.add_handler(CommandHandler("ban", ban_user))
    
    # Daftarkan Perintah (Handlers) Admin
    app.add_handler(CommandHandler("restart_total", hard_restart))
    app.add_handler(CommandHandler("konfirmasi_paksa", konfirmasi_paksa))
    app.add_handler(CommandHandler("cek_antrean", cek_antrean))
    app.add_handler(CommandHandler("set_rekening", set_rekening))
    app.add_handler(CommandHandler("claim_res", claim_reservation))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("add_admin", add_admin))
    app.add_handler(CommandHandler("reset_rek", reset_rekening))
    app.add_handler(CommandHandler("remove_admin", remove_admin))
    app.add_handler(CommandHandler("list_admin", list_admin))
    app.add_handler(CommandHandler("isi_poin", isi_poin))
    app.add_handler(CommandHandler("kirim_poin", kirim_poin))
    app.add_handler(CommandHandler("set_rek_admin", set_rek_admin))
    app.add_handler(CommandHandler("cek_pembeli", cek_pembeli))

    # Daftarkan Handler Pesan & Callback
    app.add_handler(MessageHandler(filters.PHOTO, handle_bukti))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))

    print("--- SERVER BOT TELEGRAM & WEB API PORT 5001 SUKSES DIAKTIFKAN ---")
    app.run_polling()
