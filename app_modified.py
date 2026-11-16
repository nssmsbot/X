import datetime
import asyncio
import logging
import httpx
import re
import time
import json
import os
import gc
import csv
import io
import html
import sqlite3
import secrets
import hashlib
import aiosqlite
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import lru_cache
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.event.bases import SkipHandler

# ============== CONFIGURATION ==============
        

BOT_TOKEN    = "8903349571:AAGBGOp5Z7oES4hgnjkck5FMZhfCdoqRw6M"
CHANNEL_ID   = -1003954137550
OTP_GROUP_ID = -1004474161960
ADMIN_ID     =  8807521221

CHANNEL_JOIN_URL = "https://t.me/nsotpwork"
OTP_GROUP_URL    = "https://t.me/nssmsotp"

MAX_CO_ADMINS = 3

# ================= RAILWAY-SAFE PERSISTENT DATA PATHS =================
# IMPORTANT:
# - On Railway, attach a Volume and mount it (commonly at /data).
# - DATA_DIR or RAILWAY_VOLUME_MOUNT_PATH can be set to the Volume path.
# - SQLite DB, users.json, backups, etc. are stored inside this directory.
# - Without a Railway Volume, the data can be lost when the service redeploys.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Railway persistence: if a Volume is mounted, NEVER silently switch to the
# ephemeral application directory. A silent path change is what makes stock
# appear to disappear after a redeploy/restart.
_configured_data_dir = (
    os.getenv("DATA_DIR", "").strip()
    or os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
)
_is_railway = bool(
    os.getenv("RAILWAY_ENVIRONMENT")
    or os.getenv("RAILWAY_PROJECT_ID")
    or os.getenv("RAILWAY_SERVICE_ID")
)

if _configured_data_dir:
    _DATA_DIR = os.path.abspath(os.path.expanduser(_configured_data_dir))
elif _is_railway:
    # Recommended Railway Volume mount path. Set DATA_DIR=/data explicitly
    # as well when the Volume is mounted there.
    _DATA_DIR = "/data"
elif os.path.isdir("/data") and os.access("/data", os.W_OK):
    _DATA_DIR = "/data"
else:
    _DATA_DIR = os.path.join(_APP_DIR, "data")

try:
    os.makedirs(_DATA_DIR, exist_ok=True)
except OSError as e:
    if _is_railway:
        # Keep the warning very visible: falling back on Railway means the
        # database is not on the persistent Volume and can be lost.
        print(f"[Storage][WARNING] Cannot use persistent data directory {_DATA_DIR}: {e}")
        print("[Storage][WARNING] Attach a Railway Volume and mount it at /data, then set DATA_DIR=/data.")
    _DATA_DIR = os.path.join(_APP_DIR, "data")
    os.makedirs(_DATA_DIR, exist_ok=True)

print(f"[Storage] Persistent data directory: {_DATA_DIR}")

# SMS Hadi API Database File
DB_FILE = os.path.join(_DATA_DIR, "sms_database.db")
print(f"[Storage] SQLite database: {DB_FILE}")

# ── Multi-Provider Viewstats API Config ──
# SMS Hadi / Lamix / CoreSMS share the same API structure (token + url + records).
# Each provider gets its own token/url/interval stored in bot_settings, so all
# three can poll simultaneously instead of overwriting one shared value.
SMS_PROVIDERS   = ["hadi", "lamix", "coresms"]
PROVIDER_LABELS = {"hadi": "SMS Hadi", "lamix": "Lamix", "coresms": "Shark SMS"}

# ── Dynamically-added panels (via Admin Panel ➜ "Add Panel") ──
# BASE_SMS_PROVIDERS = হার্ডকোড করা original ৩টা প্যানেল (এগুলো Remove করা যাবে না)।
# SMS_PROVIDERS list runtime এ mutate হয় (append/remove) যখন admin নতুন panel
# add/remove করে — কোড এডিট বা bot restart ছাড়াই নতুন panel চালু হয়ে যায়।
BASE_SMS_PROVIDERS = list(SMS_PROVIDERS)

# ── Auto Captcha Panel system ──────────────────────────────────────────
# Some SMS panels don't give a token+URL viewstats API (the system above) —
# instead you have to actually log into their web dashboard (username +
# password + a simple math captcha) and scrape the SMS/CDR table. This is
# a completely separate panel category, managed from its own place in the
# Admin Panel ("🔐 Auto Captcha Panel"), but it shares the same 'numbers'
# stock table / balance / notification pipeline as the panels above —
# whichever panel returns the SMS first claims the number.
CAPTCHA_PANELS   = {}   # key -> {label, login_url, username, password, msg_link,
                         #        num_col_name, num_col_idx, msg_col_name, msg_col_idx,
                         #        login_status}
captcha_sessions = {}   # key -> logged-in httpx.AsyncClient (holds cookies)

# অনুমোদিত সার্ভিসেস
ALLOWED_SERVICES = {"Facebook", "WhatsApp", "Telegram", "Discord", "Instagram"}

NEWFB_VIRTUAL_SVC = "Instagram"    # UI তে দেখানো নাম
NEWFB_API_SVC     = "Facebook"     # API এ real service ID

# Earning rate per successful eligible OTP (Facebook/Instagram)
EARN_PER_OTP = 1   # TK

# Referral commission/bonus are admin-managed in bot_settings (defaults: 5% and 1 TK).

# Per-Provider API Defaults (hadi / lamix / coresms)
_DEFAULTS = {
    "hadi_api_token":       "",
    "hadi_api_url":         "",
    "hadi_check_interval":  "5",

    "lamix_api_token":      "",
    "lamix_api_url":        "",
    "lamix_check_interval": "5",

    "coresms_api_token":      "",
    "coresms_api_url":        "",
    "coresms_check_interval": "5",
    "referral_commission_rate": "0.05",
    "referral_bonus_per_refer": "1.00",
    "usd_to_bdt_rate": "125",

    # (seconds, used while there are active rented numbers). Editable from

    # Force Join System — master on/off switch (admin can flip without
    # deleting the configured channel list).
    "force_join_enabled": "1",
    # Withdrawal master switch — admin can enable/disable user withdrawals.
    "withdraw_enabled": "1",
    "country_code_enabled": "1",
    "channel_stock_alert": "1",
    "demo_otp_interval": "30",
    "bot_stock_alert": "1"}

# =======================================================
# ✅ CUSTOM EMOJI IDs
# =======================================================

# ── Number Card (Number Allocated screen) ──────────────
E_NUM_CARD_TITLE  = "5456580414254619349"   # 🎭  Number Allocated title
E_NUM_PHONE       = "5467539229468793355"   # ☎️  Number লাইনে
E_NUM_SERVICE     = "5247087775164932249"   # 🏷   Service লাইনে
E_NUM_COUNTRY     = "6114021507908767611"   # 🪩  Country লাইনে
E_NUM_WAITING     = "5386367538735104399"   # ⏳  Waiting for SMS

# ── OTP Result Card (OTP আসার পর) ─────────────────────
E_OTP_GLOBE       = "5287292843763713628"   # 🏳️  Country লাইনে
E_OTP_PHONE       = "5285238101344544669"   # 📱  Number লাইনে
E_OTP_KEY         = "5197288647275071607"   # 🔑  OTP লাইনে

# ── OTP Forward Card (Group এ forward) ────────────────
E_FWD_BELL        = "5458603043203327669"   # 📥  OTP Notification title
E_FWD_OK          = "5237715233107110042"   # ✅  Verified Successfully
E_FWD_SERVICE     = "5247087775164932249"   # 🏷  Service লাইনে
E_FWD_COUNTRY     = "5287292843763713628"   # 🏳️  Country লাইনে
E_FWD_NUMBER      = "5285238101344544669"   # 📱  Number লাইনে
E_FWD_OTP         = "5197288647275071607"   # 🔑  OTP লাইনে

# ── Service Labels ─────────────────────────────────────
E_SVC_FACEBOOK    = "5323261730283863478"   # 📕  Facebook
E_SVC_WHATSAPP    = "6003374362860718827"   # 🛡   WhatsApp
E_SVC_TELEGRAM    = "5039783602301175152"   # 📮  Telegram
E_SVC_INSTAGRAM   = "5830394890021770129"   # 📸  Instagram
E_SVC_DISCORD_SVC = "5854863164088259816"   # 🎮  Discord
E_SVC_DEFAULT     = "5415649990104092342"   # 🔷  Unknown/Default service — Skip korle ba kono match na pele ei
                                              #     custom emoji-ta e forward card e show hobe. Nijer pochonder
                                              #     Telegram Premium custom emoji id boshiye din, jotobar khushi
                                              #     change korte parben.

# =======================================================
# ✅ CUSTOM SERVICE EMOJI IDs (Manually Added / Stock Services)
# =======================================================
# Stock upload er shomoy jokhon je kono notun/custom service name type
# kore add korben (Netflix, TikTok, Uber ইত্যাদি — JOTO KHUSHI service,
# ekta limit na), niche eikhane sheigulor emoji id boshiye din — otp
# forward card e shei emoji automatic show hobe.
#
# ➜ Key obosshoi lowercase e hote hobe (stock upload er shomoy apni
#   service name jaha EXACT type korben, tar lowercase version — space
#   thakle space o rakhben). Example: "X App" type korle key "x app".
# ➜ Value te emoji id boshan (Telegram Premium custom emoji id).
#   Khali "" rakhle sheita service er jonno default 🔷 emoji e forward
#   hobe — bot break korbe na, apni jokhon khushi emoji id boshiye
#   dite parben, restart lagbe na.
# ➜ Facebook / WhatsApp / Telegram / Discord / Instagram — eigulor
#   official emoji age theke e alada bhabe set kora (E_SVC_FACEBOOK
#   etc. — upore dekhun), tai eikhane deya lagbe na. Custom naam
#   (jemon "Facebook1", "New Fb") dile bot nijei oi official emoji
#   detect kore boshiye dey.
#
# Notun kono service er jonno khali ekta line add korle e hobe:
#   "your_service": "emoji_id",
CUSTOM_SERVICE_EMOJIS = {
    # ── Already set ──────────────────────────────────────
    "discord":    "5325612636467903082",
    "x app":      "5330337435500951363",
    "snapchat":   "5330248916224983855",
    "chatgpt":    "5359726582447487916",
    "paypal":     "5364111181415996352",
    "tiktok":     "5271527792641595125",
    "tinder":     "5969908145194015215",
    "ebay":       "5294292974736253662",
    "apple":      "5334955749409834455",
    "linkedin":   "5346024520081751155",

    # ── Streaming / Entertainment ────────────────────────
    "netflix":       "5418026554422750284",
    "youtube":       "5814161253672687027",
    "spotify":       "6008235948012211945",
    "disney+":       "5969720949044424676",
    "hbo max":       "5298588152485651370",
    "prime video":   "4961180134506758889",
    "hulu":          "4958754525956539026",
    "twitch":        "5221955907176394052",
    "soundcloud":    "5345844509412444249",

    # ── Ride / Delivery ───────────────────────────────────
    "uber":          "5298715455316303708",
    "uber eats":     "5030772108079138100",
    "pathao":        "",
    "foodpanda":     "",
    "doordash":      "",
    "grab":          "",
    "careem":        "",
    "bolt":          "",

    # ── E-commerce ────────────────────────────────────────
    "amazon":        "5323624199753842832",
    "alibaba":       "5456165576248410554",
    "aliexpress":    "5431493673487447776",
    "shopee":        "6084363470239698686",
    "daraz":         "",
    "olx":           "5355030306292249559",

    # ── Payments / Finance / Crypto ──────────────────────
    "bkash":         "6089033864922011301",
    "nagad":         "6183475123404675326",
    "rocket":        "6086803441160558654",
    "binance":       "6087120955207851591",
    "coinbase":      "5393583096677286721",
    
    
    # ── Messaging / Social ────────────────────────────────
    "imo":           "5226479577185949100",
    "skype":         "5328175271654736902",
    "viber":         "5280623335078634360",
    "wechat":        "6039372326309466262",
    "line":          "5226913613695982739",
    "signal":        "5328050550099427291",
    "threads":       "5776284173312463225",
    "reddit":        "5303103765136563815",
    "pinterest":     "5206525339517344010",
    "kik":           "",

    # ── Dating ────────────────────────────────────────────
    "bumble":        "",
    "hinge":         "",
    "badoo":         "",
    "grindr":        "",

    # ── Email / Cloud / Productivity ─────────────────────
    "gmail":         "6118546560897781055",
    "outlook":       "5267375637303145924",
    "yahoo":         "5046436450808104049",
    "icloud":        "6273556792513402505",
    "dropbox":       "5372994299065550645",
    "onedrive":      "",
    "microsoft":     "5469730010682114102",
    "google":        "5069075201950483359",
    "zoom":          "5881799193219043268",
    "slack":         "5469804317911303648",
    "notion":        "5409364537394606966",
    "canva":         "5076038705441932295",
    "adobe":         "5316632894939093711",
    "github":        "5417836094098007862",

    # ── Gaming ────────────────────────────────────────────
    "steam":         "5212957246316641686",
    "playstation":   "5373306783706137993",
    "xbox":          "5373146590015939135",
    "roblox":        "5388921730016240894",
    "epic games":    "5190877923254492865",

    # ── Travel / Booking ──────────────────────────────────
    "airbnb":        "5958810967608398168",
    "booking.com":   "",
    "agoda":         "",
    "expedia":       "",

    # ── Freelance / Work ──────────────────────────────────
    "upwork":        "",
    "fiverr":        ""}

# =======================================================
# ✅ OTP FORWARD CARD — SERVICE EMOJI IDs (Group Card ONLY)
# =======================================================
# Eta shudhu-i OTP Forward Card (OTP_GROUP_ID e forward hoya card)-er
# service icon-er jonno — alada, independent dictionary. Uporer
# CUSTOM_SERVICE_EMOJIS dictionary-ta number card / personal OTP result
# card / admin panel-er onno shob jaygay use hoy; kintu Forward Card
# eikhan theke, shudhu eikhan theke emoji nay — tai onno kono card e
# effect porbe na.
#
# ➜ Shuru-te CUSTOM_SERVICE_EMOJIS er copy diye bosano ache (ekhon-i
#   existing emoji gulo change hobe na), kintu ekhon theke সম্পূর্ণ
#   আলাদা — eta edit korle shudhu Forward Card-e effect porbe.
# ➜ Kono picker/admin-panel theke select kora lagbe na — eikhane sorasori
#   emoji id boshiye dile-i Forward Card e shei emoji show hobe.
# ➜ Key obosshoi lowercase e hote hobe, exact service name (jeta stock
#   upload er shomoy type kora hoyeche) er lowercase version. Value te
#   Telegram Premium custom emoji id boshan. Khali "" rakhle default 🔷
#   emoji show hobe — bot break korbe na.
# ➜ Facebook / WhatsApp / Telegram / Discord / Instagram — eigulor
#   official emoji (E_SVC_FACEBOOK etc.) automatic e use hoy, eikhane
#   deya lagbe na.
#
# Notun kono service er jonno khali ekta line add korle e hobe:
#   "your_service": "emoji_id",
FORWARD_CARD_SERVICE_EMOJIS = dict(CUSTOM_SERVICE_EMOJIS)

# =======================================================
# ✅ CUSTOM EMOJI FOR LANGUAGE TAG (OTP Forward Card)
# =======================================================
# Age ekhane language onujay alada alada country-flag emoji dekhano hoto.
# Ekhon shudhu EKTA fixed custom emoji use hobe (country flag na) —
# language jeta-i hok (English/Arabic/Bengali/etc.), card e shobshomoy
# eki emoji ta e dekhabe.
#
# ➜ Nijer pochondo moto Telegram Premium custom emoji id ekhane din.
# ➜ Khali "" rakhle fallback unicode emoji (🏷️) dekhabe, bot break korbe na.
E_FWD_LANG_TAG = "5388632425314140043"   # ⬅️ apnar pochonder custom emoji id ekhane din

def lang_tag(lang: str) -> str:
    """OTP Forward Card e detect_language() theke pawa language name
    er shathe ekta fixed, user-defined custom emoji jog kore
    (country flag na — sheta ekhon r use hoy na)."""
    return f"{ce(E_FWD_LANG_TAG, '🏷️')} <b>{lang}</b>"

# =======================================================
# ── Main Menu ──────────────────────────────────────────
E_MENU_WAVE       = "5379722665982434115"   # 👋  Hello
E_MENU_BOLT       = "6267107057304868214"   # ⚡  Instant Delivery
E_MENU_GLOBE2     = "6188045471118790922"   # 🪩  Global Numbers
E_MENU_LOCK       = "6158892349805040268"   # 🔐  Auto OTP Detection
E_MENU_PIN        = "6222199583632528412"   # 📌  Select service

# ── Service Selection screen ───────────────────────────
E_SVC_LIST        = "5406745015365943482"   # 📋  Select a service

# ── Range List screen ──────────────────────────────────
E_RANGE_PHONE     = "5436399161794639367"   # 📱  Range button এ

# ── Join / Verify screen ───────────────────────────────
E_JOIN_WAVE       = "5438151340947681434"   # 👋  Welcome
E_JOIN_ROBOT      = "5355051922862653659"   # 🤖  Bot mention
E_JOIN_CHANNEL    = "5424818078833715060"   # 📢  Channel
E_JOIN_GROUP      = "5303138782004924588"   # 👥  Group

# ── Admin Panel ────────────────────────────────────────
E_ADMIN_TOOL      = "5350444732919076146"   # 🛠   Admin Dashboard title
E_ADMIN_DATE      = "5413582255408815156"   # 📅  Date
E_ADMIN_USERS     = "6158914627800404063"   # 👥  Users
E_ADMIN_OTP       = "6158892349805040268"   # 📲  OTP
E_ADMIN_BOLT      = "5443127283898405358"   # ⚡  Active Sessions
E_ADMIN_WRENCH    = "6206108815075579644"   # 🔧  Admin/tool icon
E_ADMIN_CASH = "5256050517213211219"
E_DEMO_OTP       = "🧪"   # Unique DEMO OTP button/logo

# ── Reply Keyboard Buttons ─────────────────────────────
E_RK_GET_NUM      = "5303449763406954093"   # 📱  Get Number
E_RK_REFER_EARN   = "5264942233387285985"   # 👥  Refer & Earn

# ── Miscellaneous ──────────────────────────────────────
E_BROADCAST       = "6091455084015653627"   # 📣  Broadcast
E_BROADCAST_USERS = "5285015115232462979"   # 👥  Total users in broadcast
E_BROADCAST_DONE  = "5213342573602552671"   # ✅  Broadcast complete
E_BROADCAST_SEND  = "5470017648936898423"   # 📨  Sent count
E_BROADCAST_FAIL  = "5384234898494088007"   # ❌  Failed count
E_BROADCAST_BIN   = "5231339530249845638"
# Others Custom Emojis 
E_DEV_TOOL           = "5372878077250519677" # developer emoji
E_TOOL_BACKBUTTON    = "6206505206197261313" # back button 
E_TOOL_REFRESHING    = "5386367538735104399" # refresh button 
E_TOOL_CHANGENUMBER  = "5244758760429213978" # change number 
E_MENU_HOME2         = "5278702045883292456" # home button
E_MENU_OTPGROUP      = "5472239203590888751" # number card otp group button

# withdrawal menu emoji
E_WD_BINANCE =  "6087120955207851591"
E_WD_BKASH = "6089033864922011301"


# =======================================================
# 🚩 CUSTOM FLAG EMOJI IDs
# =======================================================
E_FLAG_EG  = "5293992082212409502"
E_FLAG_SS  = "5433711929606551683"
E_FLAG_MA  = "5292108962391414885"
E_FLAG_DZ  = "5294048127240655242"
E_FLAG_TN  = "5294484680601521871"
E_FLAG_LY  = "5291858711826946840"
E_FLAG_GM  = "5294399820637688352"
E_FLAG_SN  = "5292087023698466689"
E_FLAG_MR  = "5294429743674840973"
E_FLAG_ML  = "5292086972158858331"
E_FLAG_GN  = "5291892096607739008"
E_FLAG_CI  = "5293991322003200135"
E_FLAG_BF  = "5294153164960848949"
E_FLAG_NE  = "5291809418487290691"
E_FLAG_TG  = "5294097669688415562"
E_FLAG_BJ  = "5293984969746566866"
E_FLAG_MU  = "5294127824653797277"
E_FLAG_LR  = "5291793810576137439"
E_FLAG_SL  = "5294494314213167952"
E_FLAG_GH  = "5294347396266873249"
E_FLAG_NG  = "5294456308047563965"
E_FLAG_TD  = "5291780728105753403"
E_FLAG_CF  = "5294210571493724819"
E_FLAG_CM  = "5291997306126626950"
E_FLAG_CV  = "5292203503211535593"
E_FLAG_ST  = "5292183188016222701"
E_FLAG_GQ  = "5292170045416297012"
E_FLAG_GA  = "5294321325815389139"
E_FLAG_CG  = "5294035229453865597"
E_FLAG_CD  = "5431703839122141424"
E_FLAG_AO  = "5294516785482062829"
E_FLAG_GW  = "5294409819321550432"
E_FLAG_SC  = "5291891186074672309"
E_FLAG_SD  = "5294177148058228060"
E_FLAG_RW  = "5294191265615729158"
E_FLAG_ET  = "5292245976143124155"
E_FLAG_SO  = "5294058817414255960"
E_FLAG_DJ  = "5294127214768468283"
E_FLAG_KE  = "5292111852904416801"
E_FLAG_TZ  = "5292146096678658977"
E_FLAG_UG  = "5294192317882716626"
E_FLAG_BI  = "5294051631933967760"
E_FLAG_MZ  = "5294086708931874940"
E_FLAG_ZM  = "5294100109229838880"
E_FLAG_MG  = "5291991568050312348"
E_FLAG_ZW  = "5294422158762592930"
E_FLAG_NA  = "5292021761670404922"
E_FLAG_MW  = "5294241881805312589"
E_FLAG_LS  = "5292040693886247604"
E_FLAG_BW  = "5294026179957772585"
E_FLAG_SZ  = "5294312482477724867"
E_FLAG_KM  = "5294351381996521508"
E_FLAG_ZA  = "5294325281480266304"
E_FLAG_GR  = "5291948395039054764"
E_FLAG_NL  = "5291917797692042265"
E_FLAG_BE  = "5291774466043435275"
E_FLAG_FR  = "5291817660529533837"
E_FLAG_ES  = "5294513087515216901"
E_FLAG_GI  = "5292014752283774878"
E_FLAG_PT  = "5294436555492973610"
E_FLAG_LU  = "5294423709245787718"
E_FLAG_IE  = "5294471971793293647"
E_FLAG_IS  = "5294354358408859664"
E_FLAG_AL  = "5294202819077756005"
E_FLAG_MT  = "5294532213004588353"
E_FLAG_CY  = "5294062721539526918"
E_FLAG_FI  = "5294049961191690629"
E_FLAG_BG  = "5294308947719640437"
E_FLAG_LT  = "5294343084119708700"
E_FLAG_LV  = "5294343084119708700"
E_FLAG_EE  = "5291951143818123103"
E_FLAG_MD  = "5294158486425325375"
E_FLAG_AM  = "5291978717508164018"
E_FLAG_BY  = "5294134426018536120"
E_FLAG_AD  = "5294215205763434181"
E_FLAG_MC  = "5294378161117614233"
E_FLAG_SM  = "5292147350809106831"
E_FLAG_UA  = "5436077425794494759"
E_FLAG_RS  = "5294458584380230360"
E_FLAG_ME  = "5913239436157522151"
E_FLAG_HR  = "5291999676948569127"
E_FLAG_SI  = "5294279359689938006"
E_FLAG_BA  = "5433991338703991663"
E_FLAG_MK  = "5294023611567332075"
E_FLAG_IT  = "5291826830284709120"
E_FLAG_RO  = "5294107724206856227"
E_FLAG_CH  = "5291791748991835084"
E_FLAG_CZ  = "5294242852467923382"
E_FLAG_SK  = "5294538440707166931"
E_FLAG_LI  = "5292048742654957785"
E_FLAG_AT  = "5291975174160145850"
E_FLAG_GB  = "5438509360831541461"
E_FLAG_DK  = "5294531860817268837"
E_FLAG_SE  = "5291737091238026321"
E_FLAG_NO  = "5291761718580502030"
E_FLAG_PL  = "5292190970496963836"
E_FLAG_DE  = "5292013274815028523"
E_FLAG_RU  = "5435948168753724069"
E_FLAG_US  = "5434076031164103400"
E_FLAG_FK  = "5258511838828068"
E_FLAG_BZ  = "5294171848068584842"
E_FLAG_GT  = "5294336633078831209"
E_FLAG_SV  = "5294337307388695687"
E_FLAG_HN  = "5291901034434682297"
E_FLAG_NI  = "5294240825243358100"
E_FLAG_CR  = "5292063805105263554"
E_FLAG_PA  = "5291959935616178405"
E_FLAG_HT  = "5292045130587462814"
E_FLAG_PE  = "5292099427564018941"
E_FLAG_MX  = "5294535073452809778"
E_FLAG_CU  = "5291963947115631526"
E_FLAG_AR  = "5292208210495689627"
E_FLAG_BR  = "6156849989776577474"
E_FLAG_CL  = "5294231037012888049"
E_FLAG_CO  = "5294010206974397371"
E_FLAG_VE  = "5294476442854247878"
E_FLAG_BO  = "5294201479047957700"
E_FLAG_GY  = "5292062692708736193"
E_FLAG_EC  = "5292083733753517221"
E_FLAG_PY  = "5292083733753517221"
E_FLAG_SR  = "5294396668131692138"
E_FLAG_UY  = "5291928449210932974"
E_FLAG_AW  = "5294007002928798927"
E_FLAG_GL  = "5292014752283774878"
E_FLAG_MY  = "5291858351049696702"
E_FLAG_AU  = "5294444247779399477"
E_FLAG_ID  = "5433884376838454074"
E_FLAG_PH  = "5291798075478661634"
E_FLAG_NZ  = "5294189019347833274"
E_FLAG_SG  = "5294451304410663668"
E_FLAG_TH  = "5293994384314882755"
E_FLAG_BN  = "5292098293692650297"
E_FLAG_PG  = "5291917995260533077"
E_FLAG_FJ  = "5433640560134994324"
E_FLAG_JP  = "5431626087329182684"
E_FLAG_KR  = "5436211471723804119"
E_FLAG_VN  = "5294235963340379688"
E_FLAG_HK  = "5292166459118606932"
E_FLAG_KH  = "5294225191562400452"
E_FLAG_LA  = "5291981530711746037"
E_FLAG_CN  = "5294068833277990704"
E_FLAG_BD  = "5291824687096027834"
E_FLAG_TW  = "5294095745543069603"
E_FLAG_TR  = "5293993400767367408"
E_FLAG_IN  = "5291933173674957761"
E_FLAG_PK  = "5291825606219029010"
E_FLAG_AF  = "5291937511591925566"
E_FLAG_LK  = "5292102670264328257"
E_FLAG_MM  = "5294254478944393569"
E_FLAG_MV  = "5292004203844097218"
E_FLAG_LB  = "5294193108156699621"
E_FLAG_JO  = "5291988613112814801"
E_FLAG_SY  = "5294013428199869487"
E_FLAG_IQ  = "5294325010897327367"
E_FLAG_KW  = "5292066437920218075"
E_FLAG_SA  = "5294163983983463099"
E_FLAG_YE  = "5294058972033076492"
E_FLAG_OM  = "5291813666209946812"
E_FLAG_PS  = "5294289826525238172"
E_FLAG_AE  = "5294314831824835370"
E_FLAG_IL  = "5294069056616289553"
E_FLAG_BH  = "5294108398516720753"
E_FLAG_QA  = "5292166360334357676"
E_FLAG_BT  = "5294121983498277263"
E_FLAG_MN  = "5294316532631883496"
E_FLAG_NP  = "5294458756178924088"
E_FLAG_IR  = "5294220170745630736"
E_FLAG_TJ  = "5294120269806328883"
E_FLAG_TM  = "5294098958178603764"
E_FLAG_AZ  = "5294323533428579078"
E_FLAG_GE  = "5294349389131697267"
E_FLAG_KG  = "5292091954320922577"
E_FLAG_UZ  = "5436377257461431531"
E_FLAG_GLOBAL = "5258511838828068"

# =======================================================
# Helper — custom emoji wrapper
# =======================================================
def ce(emoji_id: str, fallback: str = "⭐") -> str:
    """Render Telegram Premium custom emoji in HTML message text, with Unicode fallback."""
    return svc_ce(emoji_id, fallback)


def svc_ce(emoji_id: str, fallback: str = "🔷") -> str:
    """Render a Telegram custom emoji in message text when a valid numeric ID exists."""
    emoji_id = str(emoji_id or "").strip()
    if not emoji_id.isdigit():
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

def _button_has_unicode_emoji(text: str) -> bool:
    """Return True when a button label already contains a normal Unicode emoji."""
    for ch in str(text):
        cp = ord(ch)
        if (
            0x1F000 <= cp <= 0x1FAFF or
            0x2000 <= cp <= 0x2BFF
        ):
            return True
    return False


def _button_emoji(text: str) -> str:
    """Choose a simple non-Premium Unicode emoji for button labels."""
    raw = str(text).strip()
    low = raw.lower()
    if "premium" in low:
        return ""
    if _button_has_unicode_emoji(raw):
        return ""
    if raw.isdigit():
        return "📋 "
    mapping = (
        ("change number", "🔄 "), ("change country", "🧭 "),
        ("get number", "📱 "), ("otp group", "📩 "),
        ("delete stock", "🗑️ "), ("delete", "🗑️ "), ("stock upload", "📤 "),
        ("upload", "📤 "), ("download", "📥 "), ("withdraw", "💸 "),
        ("referral", "🔗 "), ("profile", "👤 "), ("settings", "⚙️ "),
        ("bot settings", "⚙️ "), ("panel management", "🧩 "),
        ("manage users", "👥 "), ("broadcast", "📣 "), ("service", "🛠️ "),
        ("country", "🏳️ "), ("select", "☑️ "), ("stats", "📊 "),
        ("refresh", "🔄 "), ("back", "↩️ "), ("close", "❌ "),
        ("cancel", "❌ "), ("save", "💾 "), ("start", "▶️ "),
        ("stop", "⏹️ "), ("add", "➕ "), ("remove", "➖ "),
        ("clear", "🧹 "),
        ("force join", "🔗 "), ("approve", "✅ "), ("decline", "❌ "),
    )
    for key, emoji in mapping:
        if key in low:
            return emoji
    return ""


def btn(text: str, emoji_id: str = None, **kwargs) -> dict:
    # Button labels use uppercase Latin text for a consistent panel UI.
    # Unicode emoji and non-Latin scripts remain unchanged by str.upper().
    text = str(text).upper()
    premium_emoji = bool(kwargs.pop("premium_emoji", False))
    suppress_auto_emoji = bool(kwargs.pop("suppress_auto_emoji", False))
    has_leading_emoji = bool(re.match(r"^[\W_]+", text, flags=re.UNICODE))
    prefix = "" if (suppress_auto_emoji or has_leading_emoji or premium_emoji) else _button_emoji(text)
    if prefix:
        text = prefix + text

    style = kwargs.pop("style", None)
    d = {"text": text}
    if style == "__status_red__":
        d["style"] = "danger"
    elif style == "__status_green__":
        d["style"] = "success"

    # Get Number and Change Number are always red, regardless of where
    # the buttons are rendered.
    if "GET NUMBER" in str(text).upper() or "CHANGE NUMBER" in str(text).upper():
        d["style"] = "danger"
    if premium_emoji and emoji_id:
        d["icon_custom_emoji_id"] = str(emoji_id)
    if "callback_data" in kwargs:
        d["callback_data"] = kwargs["callback_data"]
    if "url" in kwargs:
        d["url"] = kwargs["url"]
    if "copy_text" in kwargs:
        ct = kwargs["copy_text"]
        d["copy_text"] = {"text": ct.text if hasattr(ct, "text") else ct}
    return d

def markup(*rows) -> dict:
    return {"inline_keyboard": list(rows)}

def bold_button(text: str) -> str:
    """Plain button text ke Unicode Mathematical Sans-Bold e convert kore,
    jate inline keyboard button er label bold dekhay (Telegram Bot API
    buttons e HTML support kore na, tai eta e-i ekmatro upay bold korar)."""
    out = []
    for ch in text:
        if 'A' <= ch <= 'Z':
            out.append(chr(ord(ch) - ord('A') + 0x1D5D4))
        elif 'a' <= ch <= 'z':
            out.append(chr(ord(ch) - ord('a') + 0x1D5EE))
        elif '0' <= ch <= '9':
            out.append(chr(ord(ch) - ord('0') + 0x1D7EC))
        else:
            out.append(ch)
    return "".join(out)

# ── Forward Card (Group) — Channel + Get Number link constants ─────────
FWD_CHANNEL_URL     = CHANNEL_JOIN_URL
FWD_GET_NUMBER_URL  = "https://t.me/nssmsbot"

# ================= GLOBAL STATE =================
class GlobalState:
    known_users        = set()
    verified_users     = set()
    banned_users       = set()
    co_admin_ids       = set()
    pending_bc         = {}
    daily_otp_count    = 0
    daily_member_count = 0
    stats_date         = ""
    rk_visible         = set()
    otp_history        = {}
    pending_user_search = {}   # admin uid -> 'ban'|'unban'|'kick'
    deleted_user_candidates = {}  # uid -> first explicit "user not found" timestamp
    deleted_cleanup_lock = None

    # --- Active order state ---
    active_orders        = {}   # legacy/live order compatibility
    service_flow_messages = {}   # user_id -> {chat_id, message_id, service}; keeps only one active service UI
    prefix_filter_pending = {}  # user_id -> {chat_id, message_id, service, country}
    prefix_filter_active  = {}  # user_id -> {chat_id, message_id, service, country, prefix, offset}
    prefix_saved          = {}  # user_id -> {prefix, service, country}


    # --- SMS Hadi Variables ---
    hadi_settings         = {}
    sent_sms_cache        = set()
    admin_pending_actions = {} # admin uid -> action string
    admin_temp_data       = {} # temp storage during excel upload / add-panel wizard
    admin_flow_messages   = {} # admin uid -> {prompt_id, status_id}; temporary flow messages to clean
    provider_tasks        = {} # provider_key -> running asyncio Task (sms_provider_monitor loop)
    captcha_panel_tasks   = {} # captcha panel key -> running asyncio Task (captcha_panel_monitor loop)
    custom_rate_svc_list  = [] # index -> custom service name (for rate-panel callback buttons)
    dlunused_service_list = [] # index -> service name (download-unused first step)
    dlunused_country_list = [] # index -> country name (for selected download-unused service)
    delstock_countries    = [] # index -> country name (for delete-stock country step)
    delstock_services     = [] # index -> service name (for delete-stock service step)
    delstock_pairs        = [] # legacy index -> (country, service)
    delstock_page         = 0  # current direct stock-list page

    # --- Custom Stock Service → Emoji mapping (persisted in DB) ---
    # custom typed service name (lowercase, e.g. "new fb") -> emoji service
    # key (e.g. "facebook" / "netflix") — admin picks this from a list
    # right after stock upload, so OTP Forward Card shows the right emoji.
    service_emoji_map     = {}
    emoji_pick_options     = [] # index -> emoji service key (for emoji-picker callback buttons)
    
    # --- Balance & Withdrawal System State ---
    pending_withdrawal_amount  = {} # user_id -> {"method": str, "details": str} (awaiting amount)
    pending_withdrawal_method  = {} # user_id -> True (awaiting method button click)
    pending_withdrawal_details = {} # user_id -> {"method": str} (awaiting payment number/UID)

    # --- Refer & Earn System State ---
    pending_referrer = {}   # user_id -> referrer_id (captured from /start ref_<id> before verification completes)

    # --- Admin Forward Pool State ---
    # Each pool owns an uploaded number list and polls configured SMS providers
    # on its own interval before forwarding newly-arrived OTPs to the group.
    forward_pool_tasks    = {} # pool_id -> asyncio.Task

    # --- DEMO OTP State ---
    demo_otp_task          = None  # asyncio.Task for admin-controlled demo generator
    demo_otp_enabled       = False
    demo_otp_services      = set()  # legacy compatibility
    demo_otp_countries     = set()  # legacy compatibility
    demo_otp_pairs         = set()  # exact selected (service, country) pairs; empty = all uploaded
    demo_otp_next_at       = 0.0   # unix timestamp for next demo OTP
    demo_otp_interval      = 30    # seconds for selected DEMO pairs
    demo_otp_random_min     = 15    # random fallback minimum
    demo_otp_random_max     = 60    # random fallback maximum

    # --- Force Join System State (Admin Panel ➜ Force Join) ---
    force_join_channels   = {} # channel_key -> {"chat_id","label","url"} (ordered dict, insertion order)
    force_join_messages   = {} # user_id -> join-message_id; deleted automatically after all joins

state = GlobalState()

# Bangladesh local date; TODAY OTP rolls over automatically at 12:00 AM (Asia/Dhaka).
BD_TZ = ZoneInfo("Asia/Dhaka")
def bd_today():
    return datetime.now(BD_TZ).strftime("%Y-%m-%d")

def check_daily_reset():
    today = bd_today()
    if state.stats_date != today:
        state.stats_date         = today
        state.daily_otp_count    = 0
        state.daily_member_count = 0

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID or user_id in state.co_admin_ids

def _admin_guard(user_id: int) -> bool:
    """Central admin authorization guard used by all Admin Panel callbacks."""
    try:
        return is_admin(int(user_id))
    except (TypeError, ValueError):
        return False

class _AdminIdContainer:
    """Lets aiogram's F.from_user.id.in_(ADMIN_IDS) filter stay dynamic —
    membership is re-checked (including co-admins) on every update instead
    of being frozen to a single constant at import time."""
    def __contains__(self, uid):
        return is_admin(uid)

ADMIN_IDS = _AdminIdContainer()

async def load_co_admins() -> set[int]:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT value FROM bot_settings WHERE key = 'co_admin_ids'") as cur:
            row = await cur.fetchone()
    if row and row[0]:
        return {int(x) for x in row[0].split(",") if x.strip().isdigit()}
    return set()

async def save_co_admins(ids: set[int]):
    val = ",".join(str(i) for i in ids)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", ("co_admin_ids", val))
        await db.commit()


def is_banned(user_id: int) -> bool:
    return user_id in state.banned_users

# ================= SAFE EDIT HELPER =================
async def safe_edit(message, text, reply_markup=None):
    try:
        if isinstance(reply_markup, dict):
            resp = await _tg_client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                json={
                    "chat_id":      message.chat.id,
                    "message_id":   message.message_id,
                    "text":         text,
                    "parse_mode":   "HTML",
                    "reply_markup": reply_markup},
                timeout=8.0,
            )
            result = resp.json()
            if not result.get("ok"):
                err = result.get("description", "")
                if "message is not modified" not in err:
                    print(f"[safe_edit] Telegram error: {err}")
        else:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            print(f"[safe_edit] TelegramBadRequest: {e}")
    except Exception as e:
        print(f"[safe_edit] Exception: {e}")

async def _delete_all_admin_flow_messages(uid: int):
    try:
        item = dict(state.admin_flow_messages.pop(int(uid), {}) or {})
        ids = set()
        for mid in item.values():
            try:
                if mid:
                    ids.add(int(mid))
            except Exception:
                pass
        for mid in ids:
            try:
                await bot.delete_message(chat_id=int(uid), message_id=mid)
            except Exception:
                pass
    except Exception:
        pass

async def _delete_admin_flow_message(uid: int, key: str):
    """Delete one tracked temporary admin-flow message if it still exists."""
    try:
        item = state.admin_flow_messages.get(int(uid), {})
        mid = item.pop(key, None)
        if not item:
            state.admin_flow_messages.pop(int(uid), None)
        if mid:
            try:
                await bot.delete_message(chat_id=int(uid), message_id=int(mid))
            except Exception:
                pass
    except Exception:
        pass

async def _track_admin_flow_message(uid: int, key: str, message):
    try:
        state.admin_flow_messages.setdefault(int(uid), {})[key] = int(message.message_id)
    except Exception:
        pass

async def _save_force_join_message_id(user_id: int, message_id):
    """Persist the latest force-join prompt so it can be removed after restart."""
    try:
        key = f"force_join_message_{int(user_id)}"
        async with aiosqlite.connect(DB_FILE) as db:
            if message_id:
                await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, str(int(message_id))))
            else:
                await db.execute("DELETE FROM bot_settings WHERE key = ?", (key,))
            await db.commit()
    except Exception as e:
        print(f"[ForceJoin] Persist error for {user_id}: {e}")

async def _load_force_join_message_id(user_id: int):
    try:
        key = f"force_join_message_{int(user_id)}"
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)) as cur:
                row = await cur.fetchone()
        if row and str(row[0]).strip().isdigit():
            return int(row[0])
    except Exception as e:
        print(f"[ForceJoin] Load error for {user_id}: {e}")
    return None

async def _delete_persisted_force_join_message(user_id: int):
    """Delete any old Join Channel/Join OTP Group prompt, including after restart."""
    uid = int(user_id)
    old_id = state.force_join_messages.pop(uid, None)
    if old_id is None:
        old_id = await _load_force_join_message_id(uid)
    if old_id:
        try:
            await bot.delete_message(chat_id=uid, message_id=int(old_id))
        except Exception:
            pass
    await _save_force_join_message_id(uid, None)

async def _delete_admin_input_message(message):
    """Delete an admin's temporary text/file input message without breaking the flow."""
    try:
        await message.delete()
    except Exception:
        pass

async def safe_edit_by_ids(chat_id: int, message_id: int, text: str, reply_markup=None):
    """Edit a Telegram message when only chat/message IDs are available."""
    try:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        resp = await _tg_client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json=payload,
            timeout=8.0,
        )
        result = resp.json()
        if not result.get("ok"):
            err = result.get("description", "")
            if "message is not modified" not in err:
                print(f"[safe_edit_by_ids] Telegram error: {err}")
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            print(f"[safe_edit_by_ids] Exception: {e}")

# ================= USER PERSISTENCE =================
USERS_FILE = os.path.join(_DATA_DIR, "users.json")

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for uid_str in data:
            state.known_users.add(int(uid_str))
            state.verified_users.add(int(uid_str))
        return data
    except Exception as e:
        print(f"[Users] Load error: {e}")
        return {}

_last_save_time: float = 0.0
_SAVE_DEBOUNCE = 0.0  # Persist user changes immediately; Railway restarts must not lose recent users.

def save_users(users_db: dict):
    global _last_save_time
    now = time.time()
    if now - _last_save_time < _SAVE_DEBOUNCE:
        return
    _last_save_time = now
    try:
        import threading
        def _write():
            try:
                tmp = USERS_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(users_db, f, ensure_ascii=False, indent=2)
                os.replace(tmp, USERS_FILE)
            except Exception as e:
                print(f"[Users] Save error: {e}")
        threading.Thread(target=_write, daemon=True).start()
    except Exception as e:
        print(f"[Users] Save thread error: {e}")

def add_user(users_db: dict, user: types.User, chat_id: int):
    uid_str = str(chat_id)
    is_new = uid_str not in users_db
    users_db[uid_str] = {
        "chat_id":    chat_id,
        "first_name": user.first_name or "",
        "last_name":  user.last_name  or "",
        "username":   f"@{user.username}" if user.username else "",
        "joined_at":  users_db.get(uid_str, {}).get("joined_at", datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")),
        "last_seen":  datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")}
    state.known_users.add(chat_id)
    if is_new:
        check_daily_reset()
        state.daily_member_count += 1
        save_users(users_db)
    return is_new

users_db: dict = {}


# ================= DELETED ACCOUNT CLEANUP (SAFE MODE) =================
# IMPORTANT SAFETY RULES:
# 1) Never delete on timeout, network error, permission error, or generic API error.
# 2) A user must return an explicit "user not found" result from get_chat().
# 3) Cleanup runs every minute; an explicit "user not found" result is sufficient.
# 4) If any configured group/channel still reports the user as a member/admin/creator,
#    cleanup is ABORTED. This protects active/original users from false positives.
# 5) Financial/OTP/referral SQLite records are NEVER deleted by this feature.
DELETED_CLEANUP_FILE = os.path.join(_DATA_DIR, "deleted_account_candidates.json")
DELETED_CONFIRM_AFTER = 0
DELETED_CLEANUP_INTERVAL = 60


def _load_deleted_candidates() -> dict:
    try:
        if not os.path.exists(DELETED_CLEANUP_FILE):
            return {}
        with open(DELETED_CLEANUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[DeletedCleanup] Load error: {e}")
        return {}


def _save_deleted_candidates(data: dict):
    try:
        tmp = DELETED_CLEANUP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DELETED_CLEANUP_FILE)
    except Exception as e:
        print(f"[DeletedCleanup] Save error: {e}")


def _is_explicit_deleted_error(exc: Exception) -> bool:
    """Only accept the narrow Telegram response used for a missing user."""
    msg = str(exc).lower()
    return "user not found" in msg


async def _deleted_cleanup_probe(uid: int):
    """Return True only when Telegram explicitly says this known user is missing."""
    try:
        await bot.get_chat(int(uid))
        return False, "active"
    except Exception as e:
        if not _is_explicit_deleted_error(e):
            # Ambiguous errors are NEVER treated as deletion.
            return False, "ambiguous"

    # Independent safety check: if Telegram still exposes the user as a member
    # of any configured force-join chat, the record must not be cleaned.
    for item in state.force_join_channels.values():
        chat_id = item.get("chat_id")
        if not chat_id:
            continue
        try:
            member = await bot.get_chat_member(chat_id, int(uid))
            status = getattr(member, "status", "")
            is_member = getattr(member, "is_member", None)
            if status in ("member", "administrator", "creator") or (status == "restricted" and is_member is True):
                return False, "still_member"
        except Exception as e:
            if _is_explicit_deleted_error(e):
                continue
            # Permission/rate-limit/network/etc. => fail closed.
            return False, "ambiguous"

    return True, "confirmed_missing"


async def _cleanup_deleted_accounts_once() -> dict:
    """Conservative two-pass cleanup. No financial/OTP/referral rows are deleted."""
    if not isinstance(users_db, dict) or not users_db:
        return {"checked": 0, "quarantined": 0, "removed": 0, "skipped": 0}

    candidates = _load_deleted_candidates()
    now = time.time()
    checked = quarantined = removed = skipped = 0
    changed_candidates = False

    # Snapshot keys so the dictionary can be safely changed after a confirmed cleanup.
    for uid_str in list(users_db.keys()):
        try:
            uid = int(uid_str)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if uid <= 0:
            skipped += 1
            continue
        # Never touch the main admin or co-admins.
        if uid == int(ADMIN_ID) or uid in state.co_admin_ids:
            continue

        checked += 1
        missing, reason = await _deleted_cleanup_probe(uid)
        if not missing:
            # Any successful/ambiguous/active result clears a previous candidate.
            if uid_str in candidates:
                candidates.pop(uid_str, None)
                changed_candidates = True
            continue

        previous = candidates.get(uid_str)
        if not previous:
            candidates[uid_str] = {
                "first_seen": now,
                "checks": 1,
                "reason": reason,
            }
            quarantined += 1
            changed_candidates = True
            print(f"[DeletedCleanup] Quarantined uid={uid}: explicit user-not-found (pass 1).")
            continue

        first_seen = float(previous.get("first_seen", now) or now)
        previous["checks"] = int(previous.get("checks", 1) or 1) + 1
        previous["last_seen"] = now
        changed_candidates = True

        # Final live check immediately before destructive action.
        final_missing, final_reason = await _deleted_cleanup_probe(uid)
        if not final_missing:
            candidates.pop(uid_str, None)
            continue

        # Remove ONLY local identity/verification state. Keep all financial,
        # OTP and referral history intact for audit/accounting purposes.
        users_db.pop(uid_str, None)
        state.known_users.discard(uid)
        state.verified_users.discard(uid)
        state.rk_visible.discard(uid)
        state.banned_users.discard(uid)
        candidates.pop(uid_str, None)
        removed += 1
        changed_candidates = True
        print(f"[DeletedCleanup] Removed confirmed deleted account uid={uid} from users.json/state.")

    if changed_candidates:
        _save_deleted_candidates(candidates)
    if removed:
        save_users(users_db)

    return {"checked": checked, "quarantined": quarantined, "removed": removed, "skipped": skipped}


async def deleted_account_cleanup_loop():
    """Run conservative cleanup periodically; all errors fail closed."""
    print("[DeletedCleanup] Safe cleanup loop started.")
    while True:
        try:
            result = await _cleanup_deleted_accounts_once()
            print(f"[DeletedCleanup] {result}")
            await asyncio.sleep(DELETED_CLEANUP_INTERVAL)
        except asyncio.CancelledError:
            print("[DeletedCleanup] Loop cancelled.")
            return
        except Exception as e:
            print(f"[DeletedCleanup] Loop error (no deletion performed by this error): {e}")
            await asyncio.sleep(60)

# ================= DATABASE (SMS Hadi Stock & Earnings) =================
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=FULL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA secure_delete=ON")
        await db.execute("CREATE TABLE IF NOT EXISTS sent_sms (sms_hash TEXT PRIMARY KEY)")
        await db.execute('''
            CREATE TABLE IF NOT EXISTS numbers (
                phone_number TEXT,
                service TEXT,
                country TEXT,
                status TEXT DEFAULT 'available',
                assigned_to INTEGER DEFAULT NULL,
                assign_time INTEGER DEFAULT NULL,
                otp_rate REAL DEFAULT NULL,
                otp_received INTEGER DEFAULT 0,
                sms_seen_count INTEGER DEFAULT 0,
                PRIMARY KEY (phone_number, service)
            )
        ''')

        # Multi-service stock migration: older versions used phone_number as the
        # only primary key, so the same number could belong to only one service.
        # Rebuild the table once with (phone_number, service) as the composite key.
        async with db.execute("PRAGMA table_info(numbers)") as cursor:
            _ninfo = await cursor.fetchall()
        _pk_cols = [row[1] for row in _ninfo if row[5]]
        if _pk_cols == ["phone_number"]:
            await db.execute("ALTER TABLE numbers RENAME TO numbers_legacy_single_service")
            await db.execute('''
                CREATE TABLE numbers (
                    phone_number TEXT,
                    service TEXT,
                    country TEXT,
                    status TEXT DEFAULT 'available',
                    assigned_to INTEGER DEFAULT NULL,
                    assign_time INTEGER DEFAULT NULL,
                    otp_rate REAL DEFAULT NULL,
                    otp_received INTEGER DEFAULT 0,
                    sms_seen_count INTEGER DEFAULT 0,
                    PRIMARY KEY (phone_number, service)
                )
            ''')
            await db.execute('''
                INSERT INTO numbers
                    (phone_number, service, country, status, assigned_to, assign_time, otp_rate, otp_received, sms_seen_count)
                SELECT phone_number, COALESCE(service, ''), country, status, assigned_to, assign_time, otp_rate,
                       COALESCE(otp_received, 0), COALESCE(sms_seen_count, 0)
                FROM numbers_legacy_single_service
            ''')
            await db.execute("DROP TABLE numbers_legacy_single_service")
        # Per-number OTP rate is retained for compatibility, but uploads now always
        # store the single service rate value.
        async with db.execute("PRAGMA table_info(numbers)") as cursor:
            number_cols = {row[1] for row in await cursor.fetchall()}
        if "otp_rate" not in number_cols:
            await db.execute("ALTER TABLE numbers ADD COLUMN otp_rate REAL DEFAULT NULL")

        await db.execute('''
            CREATE TABLE IF NOT EXISTS number_usage_history (
                phone_number TEXT PRIMARY KEY,
                first_used_at INTEGER NOT NULL
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        # Dynamically-added SMS panels (Admin Panel ➜ "Add Panel").
        # token/url/interval নিজেরা bot_settings এ same key-pattern এ স্টোর হয়
        # (যেমন base provider গুলো হয়) — এই টেবিলটা শুধু কোন কোন provider_key
        # dynamically add করা হয়েছে, তার registry হিসেবে কাজ করে, যাতে restart
        # এর পরেও bot সেগুলো আবার লোড করে চালু করতে পারে।
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sms_panels (
                provider_key TEXT PRIMARY KEY,
                label TEXT,
                created_at TEXT
            )
        ''')

        # Force Join channels/groups — admin-managed list of chats a user
        # must join before using the bot. "channel"/"otpgroup" rows are
        # seeded below from the original hardcoded CHANNEL_ID/OTP_GROUP_ID so
        # behaviour is unchanged on first run; admin can add more on top.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS force_join_channels (
                channel_key TEXT PRIMARY KEY,
                chat_id TEXT,
                label TEXT,
                url TEXT,
                created_at TEXT
            )
        ''')
        if CHANNEL_ID:
            await db.execute(
                "INSERT OR REPLACE INTO force_join_channels (channel_key, chat_id, label, url, created_at) VALUES (?, ?, ?, ?, ?)",
                ("channel", str(CHANNEL_ID), "Channel", FWD_CHANNEL_URL, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
        if OTP_GROUP_ID:
            await db.execute(
                "INSERT OR REPLACE INTO force_join_channels (channel_key, chat_id, label, url, created_at) VALUES (?, ?, ?, ?, ?)",
                ("otpgroup", str(OTP_GROUP_ID), "OTP Group", OTP_GROUP_URL, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )

        # Keep custom force-join channels/groups across restarts. Only seed the
        # two original targets when this function runs; do not delete custom rows.

        # Auto Captcha Panels (login-based panels, see CAPTCHA_PANELS above).
        await db.execute('''
            CREATE TABLE IF NOT EXISTS captcha_panels (
                panel_key TEXT PRIMARY KEY,
                label TEXT,
                login_url TEXT,
                username TEXT,
                password TEXT,
                msg_link TEXT,
                num_col_name TEXT DEFAULT 'number',
                num_col_idx INTEGER DEFAULT 1,
                msg_col_name TEXT DEFAULT 'message',
                msg_col_idx INTEGER DEFAULT 2,
                login_status TEXT DEFAULT '⏳ Pending First Login',
                created_at TEXT
            )
        ''')

        # ── Migration: captcha_panels টেবিলে panel_type কলাম মিসিং থাকলে যোগ করো।
        # 'generic'   → পুরনো form-scraping + DataTables/HTML table auto captcha panel।
        # 'greennews' → Green Panel (Green SMS) — আলাদা Django login + JSON API সিস্টেম।
        async with db.execute("PRAGMA table_info(captcha_panels)") as cursor:
            existing_cap_cols = {row[1] for row in await cursor.fetchall()}
        if "panel_type" not in existing_cap_cols:
            await db.execute("ALTER TABLE captcha_panels ADD COLUMN panel_type TEXT DEFAULT 'generic'")
        await db.commit()
        
        # Custom Stock Service Name → Emoji-Service mapping. Jokhon admin
        # stock upload er shomoy custom service name type kore (e.g.
        # "new fb") ar tarpor emoji-picker theke ekta service select kore
        # (e.g. Facebook), sheita eikhane save thake, jate bot restart
        # er porew OTP Forward Card e thik emoji ta e dekhay.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS service_emoji_map (
                service_name TEXT PRIMARY KEY,
                emoji_key TEXT
            )
        ''')

        # Unlimited Earning Rate service registry.
        # Services can be registered even before stock is uploaded.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS earning_services (
                service_name TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                enabled INTEGER DEFAULT 1
            )
        ''')
        # Migration for older databases created before service removal support.
        async with db.execute("PRAGMA table_info(earning_services)") as _cur:
            _cols = {row[1] for row in await _cur.fetchall()}
        if "enabled" not in _cols:
            await db.execute("ALTER TABLE earning_services ADD COLUMN enabled INTEGER DEFAULT 1")
        await db.commit()

        # Balance & Withdrawal Tables
        await db.execute('''
            CREATE TABLE IF NOT EXISTS balances (
                user_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0.0
            )
        ''')
        # Prevent the same exported balance backup from being credited twice.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS imported_balance_backups (
                backup_id TEXT PRIMARY KEY,
                imported_at TEXT NOT NULL,
                imported_by INTEGER,
                row_count INTEGER NOT NULL
            )
        """)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                method TEXT,
                details TEXT,
                amount REAL,
                status TEXT DEFAULT 'pending',
                timestamp TEXT
            )
        ''')

        # Refer & Earn Tables
        await db.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                user_id INTEGER PRIMARY KEY,
                referrer_id INTEGER,
                joined_at TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS referral_earnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                from_user_id INTEGER,
                amount REAL,
                timestamp TEXT
            )
        ''')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_refearn_referrer ON referral_earnings(referrer_id)")
        # Persist pending referral attribution across bot restarts while a new user
        # is still completing the required-channel join flow.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS pending_referrals (
                user_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL,
                created_at TEXT
            )
        ''')

        # Per-user OTP statistics. Keeps service-wise OTP receive counts
        # persistent across bot restarts and independent of the temporary
        # in-memory otp_history (which admin can clear).
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_otp_stats (
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                otp_count INTEGER DEFAULT 0,
                total_earned REAL DEFAULT 0.0,
                PRIMARY KEY (user_id, service)
            )
        ''')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_otp_stats_user ON user_otp_stats(user_id)")
        # Per-user daily OTP count. Keeps today's OTP count persistent across restarts.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_daily_otp_stats (
                user_id INTEGER NOT NULL,
                otp_date TEXT NOT NULL,
                otp_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, otp_date)
            )
        ''')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_daily_otp_user_date ON user_daily_otp_stats(user_id, otp_date)")
        # Persistent OTP totals per source panel (token/API panels and captcha panels).
        await db.execute('''
            CREATE TABLE IF NOT EXISTS panel_daily_otp_stats (
                panel_key TEXT NOT NULL,
                otp_date TEXT NOT NULL,
                otp_count INTEGER DEFAULT 0,
                PRIMARY KEY (panel_key, otp_date)
            )
        ''')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_panel_daily_otp_date ON panel_daily_otp_stats(otp_date)")
        
        # ── Migration: পুরনো withdrawals টেবিলে user_id/method/details/amount/
        # status/timestamp কলাম মিসিং থাকলে যোগ করে দাও, নইলে INSERT ক্র্যাশ করবে ──
        async with db.execute("PRAGMA table_info(withdrawals)") as cursor:
            existing_cols = {row[1] for row in await cursor.fetchall()}

        _wd_expected_cols = {
            "user_id": "INTEGER",
            "method": "TEXT",
            "details": "TEXT",
            "amount": "REAL",
            "status": "TEXT DEFAULT 'pending'",
            "timestamp": "TEXT"}
        for col_name, col_type in _wd_expected_cols.items():
            if col_name not in existing_cols:
                await db.execute(f"ALTER TABLE withdrawals ADD COLUMN {col_name} {col_type}")
        if "requested_usd" not in existing_cols:
            await db.execute("ALTER TABLE withdrawals ADD COLUMN requested_usd REAL")
        await db.commit()

        await db.execute("CREATE INDEX IF NOT EXISTS idx_numbers_status ON numbers(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_numbers_svc_country ON numbers(service, country, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_numbers_svc_country_status ON numbers(service, country, status, assigned_to)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_number_usage_history_phone ON number_usage_history(phone_number)")
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_number_usage_history_phone_normalized
            ON number_usage_history(REPLACE(REPLACE(REPLACE(REPLACE(phone_number, '+', ''), ' ', ''), '-', ''), '(', ''))
        """)
        # Fast normalized-number lookups used by the leasing path. Keep the
        # expression identical to the correlated NOT EXISTS query below so
        # SQLite can use the expression index instead of scanning all history.
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_phone_normalized_full
            ON number_usage_history(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_numbers_phone_normalized_full
            ON numbers(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
            )
        """)

        # Admin-managed uploaded number pools. These are deliberately separate
        # from the customer stock table: a forward pool is watched for incoming
        # SMS and never allocated to regular users.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS forward_pools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL DEFAULT 15,
                languages TEXT NOT NULL DEFAULT '["English"]',
                status TEXT NOT NULL DEFAULT 'draft',
                created_by INTEGER,
                created_at TEXT NOT NULL,
                started_at TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS forward_pool_numbers (
                pool_id INTEGER NOT NULL,
                phone_number TEXT NOT NULL,
                country TEXT NOT NULL,
                sms_seen_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (pool_id, phone_number),
                FOREIGN KEY (pool_id) REFERENCES forward_pools(id) ON DELETE CASCADE
            )
        ''')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_forward_pool_status ON forward_pools(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_forward_pool_numbers_pool ON forward_pool_numbers(pool_id)")
        await db.commit()

        # ── Migration: numbers টেবিলে otp_received কলাম মিসিং থাকলে যোগ করো।
        # Eta die track kora hoy ekta number e already ekta OTP eshe geche kina —
        # OTP asle number ta ar 'available' pool e ferot jabe na (permanently
        # retired thakbe), kintu 'busy' status e-i theke jabe jate porer je kono
        # SMS-o watch/forward hote thake, first SMS-e loop bondho hoye jay na. ──
        async with db.execute("PRAGMA table_info(numbers)") as cursor:
            existing_num_cols = {row[1] for row in await cursor.fetchall()}
        if "otp_received" not in existing_num_cols:
            await db.execute("ALTER TABLE numbers ADD COLUMN otp_received INTEGER DEFAULT 0")
        if "sms_seen_count" not in existing_num_cols:
            # Eta die proti number e ekhon obdi koyta SMS forward kora hoyeche
            # seta track kora hoy — content/timestamp er upor depend na kore
            # purely count-based dedup korar jonno (nichey monitor loop e use hoy).
            await db.execute("ALTER TABLE numbers ADD COLUMN sms_seen_count INTEGER DEFAULT 0")
        await db.execute(
            "INSERT OR IGNORE INTO number_usage_history (phone_number, first_used_at) "
            "SELECT phone_number, COALESCE(assign_time, strftime('%s','now')) "
            "FROM numbers WHERE status IN ('busy', 'retired')"
        )

        # HARD GLOBAL ONE-TIME NUMBER USE PROTECTION.
        # Normalize phone numbers before comparing, so +, spaces, hyphens,
        # and parentheses cannot make the same number appear new. Recreate
        # triggers on startup so existing databases receive the corrected rules.
        await db.execute("DROP TRIGGER IF EXISTS trg_numbers_no_reuse_insert")
        await db.execute("DROP TRIGGER IF EXISTS trg_numbers_no_reuse_busy")
        await db.execute("DROP TRIGGER IF EXISTS trg_numbers_no_reuse_available")
        await db.execute("DROP TRIGGER IF EXISTS trg_numbers_record_use_insert")
        await db.execute("DROP TRIGGER IF EXISTS trg_numbers_record_use_busy")
        await db.execute("""
            CREATE TRIGGER trg_numbers_no_reuse_insert
            BEFORE INSERT ON numbers
            WHEN NEW.status = 'busy'
             AND EXISTS (
                 SELECT 1 FROM number_usage_history h
                 WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
                     = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(NEW.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
             )
            BEGIN
                SELECT RAISE(ABORT, 'number_already_used');
            END;
        """)
        await db.execute("""
            CREATE TRIGGER trg_numbers_no_reuse_busy
            BEFORE UPDATE OF status ON numbers
            WHEN NEW.status = 'busy'
             AND OLD.status <> 'busy'
             AND EXISTS (
                 SELECT 1 FROM number_usage_history h
                 WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
                     = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(NEW.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
             )
            BEGIN
                SELECT RAISE(ABORT, 'number_already_used');
            END;
        """)
        await db.execute("""
            CREATE TRIGGER trg_numbers_no_reuse_available
            BEFORE UPDATE OF status ON numbers
            WHEN NEW.status = 'available'
             AND EXISTS (
                 SELECT 1 FROM number_usage_history h
                 WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
                     = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(OLD.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
             )
            BEGIN
                SELECT RAISE(ABORT, 'number_already_used');
            END;
        """)
        # DB-level audit: any allocation through any code path permanently
        # records the number as used, even if an application handler forgets.
        await db.execute("""
            CREATE TRIGGER trg_numbers_record_use_insert
            AFTER INSERT ON numbers
            WHEN NEW.status = 'busy'
            BEGIN
                INSERT OR IGNORE INTO number_usage_history (phone_number, first_used_at)
                VALUES (NEW.phone_number, COALESCE(NEW.assign_time, CAST(strftime('%s','now') AS INTEGER)));
            END;
        """)
        await db.execute("""
            CREATE TRIGGER trg_numbers_record_use_busy
            AFTER UPDATE OF status ON numbers
            WHEN NEW.status = 'busy' AND OLD.status <> 'busy'
            BEGIN
                INSERT OR IGNORE INTO number_usage_history (phone_number, first_used_at)
                VALUES (NEW.phone_number, COALESCE(NEW.assign_time, CAST(strftime('%s','now') AS INTEGER)));
            END;
        """)
        await db.commit()

        # Insert defaults for SMS Hadi API
        for k, v in _DEFAULTS.items():
            await db.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)", (k, v))
        await db.commit()
    
    await load_settings()
    await load_dynamic_panels()
    await load_captcha_panels()
    await load_service_emoji_map()
    await load_force_join_channels()
    
    # Load sent hashes into in-memory set cache for instant hot-path lookup
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT sms_hash FROM sent_sms") as cursor:
            rows = await cursor.fetchall()
            state.sent_sms_cache.update(row[0] for row in rows)

async def load_settings():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT key, value FROM bot_settings") as cursor:
            rows = await cursor.fetchall()
    state.hadi_settings = {row[0]: row[1] for row in rows}
    try:
        state.demo_otp_interval = max(1, min(int(state.hadi_settings.get("demo_otp_interval", "30")), 3600))
    except Exception:
        state.demo_otp_interval = 30

async def load_service_emoji_map():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT service_name, emoji_key FROM service_emoji_map") as cursor:
            rows = await cursor.fetchall()
    state.service_emoji_map = {row[0]: row[1] for row in rows}

async def set_service_emoji(service_name: str, emoji_key: str):
    """Custom typed service name (e.g. 'new fb') ke ekta emoji-service key
    (e.g. 'facebook') er shathe permanently bind kore — DB + in-memory
    cache dutoi update hoy, tai forward card e shathe shathe emoji thik hoye jay."""
    key = service_name.strip().lower()
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO service_emoji_map (service_name, emoji_key) VALUES (?, ?)", (key, emoji_key))
        await db.commit()
    state.service_emoji_map[key] = emoji_key

def get_provider_token(provider: str) -> str:
    return state.hadi_settings.get(f"{provider}_api_token", _DEFAULTS.get(f"{provider}_api_token", ""))

def get_provider_url(provider: str) -> str:
    val = state.hadi_settings.get(f"{provider}_api_url")
    if val:
        return val
    if provider == "hadi":
        # backward-compat: older single-provider builds stored this under "api_url"
        legacy = state.hadi_settings.get("api_url")
        if legacy:
            return legacy
    return _DEFAULTS.get(f"{provider}_api_url", "")

def get_provider_interval(provider: str) -> int:
    try:
        val = state.hadi_settings.get(f"{provider}_check_interval")
        if val is None and provider == "hadi":
            val = state.hadi_settings.get("check_interval")  # backward-compat
        if val is None:
            val = _DEFAULTS.get(f"{provider}_check_interval", "1")
        # Provider APIs can rate-limit aggressive polling (HTTP 429).
        # Keep the configured interval, but never hammer Viewstats faster than 5s.
        return max(5, int(val))
    except Exception:
        return 1

def provider_configured(provider: str) -> bool:
    return bool(get_provider_token(provider) and get_provider_url(provider))

# One persistent HTTP client per provider: avoids reconnecting every second.
PROVIDER_HTTP_CLIENTS: dict[str, httpx.AsyncClient] = {}

async def get_provider_http_client(provider: str) -> httpx.AsyncClient:
    client = PROVIDER_HTTP_CLIENTS.get(provider)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0), limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))
        PROVIDER_HTTP_CLIENTS[provider] = client
    return client

async def close_provider_http_clients():
    for client in list(PROVIDER_HTTP_CLIENTS.values()):
        try:
            await client.aclose()
        except Exception:
            pass
    PROVIDER_HTTP_CLIENTS.clear()

# ── Dynamic Panel Management ──────────────────────────────────────────
# "Add Panel" button দিয়ে admin যখন নতুন SMS provider (token + url) যোগ করে,
# এই ফাংশনগুলো সেটা DB তে সেভ করে, in-memory SMS_PROVIDERS/PROVIDER_LABELS
# আপডেট করে, এবং সাথে সাথে (কোনো restart ছাড়াই) তার জন্য একটা নতুন
# background monitor loop চালু করে দেয় — অন্য panel গুলোর সাথে parallel ভাবে।
async def load_dynamic_panels():
    """Bot চালু হওয়ার সময় আগে থেকে DB তে সেভ করা panel গুলো লোড করে।"""
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT provider_key, label FROM sms_panels") as cursor:
            rows = await cursor.fetchall()
    for key, label in rows:
        if key not in SMS_PROVIDERS:
            SMS_PROVIDERS.append(key)
        PROVIDER_LABELS[key] = label

def sanitize_panel_key(name: str) -> str:
    """Panel এর নাম থেকে safe, unique provider_key বানায় (শুধু lowercase a-z0-9)।"""
    return re.sub(r"[^a-z0-9]", "", name.strip().lower())

_RESERVED_PANEL_KEYS = {"rate", "stockrate", "min", "withdraw", "c"}

# ── Force Join System ──────────────────────────────────────────────────
# "channel" / "otpgroup" = the two original hardcoded force-join targets
# (CHANNEL_ID / OTP_GROUP_ID) — always present, can't be removed from admin
# and can't be reused as the key for a newly-added channel.
BASE_FORCE_JOIN_KEYS = {"channel", "otpgroup"}

async def add_dynamic_panel(key: str, label: str, token: str, url: str, interval: str = "1"):
    """নতুন panel DB তে persist করে এবং তার monitor loop এখনই চালু করে দেয়।"""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO sms_panels (provider_key, label, created_at) VALUES (?, ?, ?)",
            (key, label, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (f"{key}_api_token", token))
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (f"{key}_api_url", url))
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (f"{key}_check_interval", interval))
        await db.commit()

    state.hadi_settings[f"{key}_api_token"]      = token
    state.hadi_settings[f"{key}_api_url"]        = url
    state.hadi_settings[f"{key}_check_interval"] = interval

    if key not in SMS_PROVIDERS:
        SMS_PROVIDERS.append(key)
    PROVIDER_LABELS[key] = label

    # Restart ছাড়াই এখনই এই panel এর জন্য একটা নতুন monitor loop চালু করে দাও —
    # বাকি সব panel এর পাশাপাশি parallel ভাবে চলবে।
    old_task = state.provider_tasks.get(key)
    if old_task and not old_task.done():
        old_task.cancel()
    state.provider_tasks[key] = asyncio.create_task(sms_provider_monitor(key), name=f"{key}_sms_monitor")

async def remove_dynamic_panel(key: str):
    """একটা manually-added panel বন্ধ করে ও পুরোপুরি মুছে ফেলে। Base ৩টা panel remove করা যায় না (কলার নিজে চেক করবে)।"""
    task = state.provider_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM sms_panels WHERE provider_key = ?", (key,))
        for suffix in ("_api_token", "_api_url", "_check_interval"):
            await db.execute("DELETE FROM bot_settings WHERE key = ?", (f"{key}{suffix}",))
        await db.commit()

    for suffix in ("_api_token", "_api_url", "_check_interval"):
        state.hadi_settings.pop(f"{key}{suffix}", None)
    PROVIDER_LABELS.pop(key, None)
    if key in SMS_PROVIDERS:
        SMS_PROVIDERS.remove(key)

# ── Force Join Management (persist + toggle) ────────────────────────────
async def load_force_join_channels():
    """Bot চালু হওয়ার সময় DB তে সেভ করা force-join channel/group গুলো লোড করে।"""
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT channel_key, chat_id, label, url FROM force_join_channels "
            "ORDER BY created_at, channel_key"
        ) as cursor:
            rows = await cursor.fetchall()
    state.force_join_channels = {
        key: {"chat_id": chat_id, "label": label, "url": url}
        for key, chat_id, label, url in rows
    }

def sanitize_force_join_key(name: str) -> str:
    """Channel/group নাম থেকে safe, unique key বানায় (শুধু lowercase a-z0-9)।"""
    return re.sub(r"[^a-z0-9]", "", name.strip().lower())

async def add_force_join_channel(key: str, chat_id: str, label: str, url: str):
    """Add/update any admin-configured force-join channel or group and persist it."""
    key = sanitize_force_join_key(key)
    chat_id = str(chat_id or "").strip()
    label = str(label or "").strip()
    url = str(url or "").strip()
    if not key or not chat_id or not label or not re.match(r"^https?://t\.me/", url, re.I):
        return False
    # Preserve the original targets' canonical identifiers and labels.
    if key == "channel":
        chat_id, label = str(CHANNEL_ID), "Channel"
    elif key == "otpgroup":
        chat_id, label = str(OTP_GROUP_ID), "OTP Group"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO force_join_channels (channel_key, chat_id, label, url, created_at) VALUES (?, ?, ?, ?, ?)",
            (key, chat_id, label, url, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()
    state.force_join_channels[key] = {"chat_id": chat_id, "label": label, "url": url}
    return True

async def remove_force_join_channel(key: str):
    """একটা manually-added force-join channel মুছে ফেলে। Base ২টা (channel/otpgroup)
    remove করা যায় না (কলার নিজে চেক করবে)।"""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM force_join_channels WHERE channel_key = ?", (key,))
        await db.commit()
    state.force_join_channels.pop(key, None)

def force_join_enabled() -> bool:
    return state.hadi_settings.get("force_join_enabled", "1") == "1"

async def set_force_join_enabled(enabled: bool):
    val = "1" if enabled else "0"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", ("force_join_enabled", val))
        await db.commit()
    state.hadi_settings["force_join_enabled"] = val

# ── Auto Captcha Panel Management (persist + spawn/kill monitor loop) ──
async def load_captcha_panels():
    """Bot চালু হওয়ার সময় আগে থেকে সেভ করা Auto Captcha Panel গুলো লোড করে।"""
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT panel_key, label, login_url, username, password, msg_link, "
            "num_col_name, num_col_idx, msg_col_name, msg_col_idx, login_status, panel_type FROM captcha_panels"
        ) as cursor:
            rows = await cursor.fetchall()
    for row in rows:
        key = row[0]
        CAPTCHA_PANELS[key] = {
            "label": row[1], "login_url": row[2], "username": row[3], "password": row[4],
            "msg_link": row[5] or "", "num_col_name": row[6] or "number", "num_col_idx": row[7] or 1,
            "msg_col_name": row[8] or "message", "msg_col_idx": row[9] or 2,
            "login_status": row[10] or "⏳ Pending First Login",
            "panel_type": row[11] or "generic"}

async def add_captcha_panel(key: str, label: str, login_url: str, username: str, password: str,
                             msg_link: str = "", num_col_name: str = "number", num_col_idx: int = 1,
                             msg_col_name: str = "message", msg_col_idx: int = 2, panel_type: str = "generic"):
    """নতুন Auto Captcha Panel DB তে persist করে এবং তার monitor loop এখনই চালু করে দেয়।
    panel_type='generic' → পুরনো form-scraping panel। panel_type='greennews' → Green Panel।"""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO captcha_panels "
            "(panel_key, label, login_url, username, password, msg_link, num_col_name, num_col_idx, "
            "msg_col_name, msg_col_idx, login_status, created_at, panel_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, label, login_url, username, password, msg_link, num_col_name, num_col_idx,
             msg_col_name, msg_col_idx, "⏳ Pending First Login", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             panel_type)
        )
        await db.commit()

    CAPTCHA_PANELS[key] = {
        "label": label, "login_url": login_url, "username": username, "password": password,
        "msg_link": msg_link, "num_col_name": num_col_name, "num_col_idx": num_col_idx,
        "msg_col_name": msg_col_name, "msg_col_idx": msg_col_idx, "login_status": "⏳ Pending First Login",
        "panel_type": panel_type}

    # Restart ছাড়াই এখনই এই panel এর জন্য একটা নতুন monitor loop চালু করে দাও।
    state.captcha_panel_tasks[key] = asyncio.create_task(captcha_panel_monitor(key), name=f"{key}_captcha_monitor")

async def remove_captcha_panel(key: str):
    """একটা Auto Captcha Panel বন্ধ করে ও পুরোপুরি মুছে ফেলে (generic অথবা Green Panel — দুটোই)।"""
    task = state.captcha_panel_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()
    old_session = captcha_sessions.pop(key, None)
    if old_session is not None:
        # generic panel সেশন সরাসরি httpx.AsyncClient; Green Panel সেশন
        # {"client":..., "base_url":...} dict — দুটোই safely close করার চেষ্টা করি।
        client_to_close = old_session.get("client") if isinstance(old_session, dict) else old_session
        if client_to_close is not None:
            try:
                asyncio.create_task(client_to_close.aclose())
            except Exception:
                pass

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM captcha_panels WHERE panel_key = ?", (key,))
        await db.commit()

    CAPTCHA_PANELS.pop(key, None)

async def _update_captcha_login_status(key: str, status: str):
    if key in CAPTCHA_PANELS:
        CAPTCHA_PANELS[key]["login_status"] = status
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE captcha_panels SET login_status = ? WHERE panel_key = ?", (status, key))
        await db.commit()

# Backward-compat aliases (old code paths still call these for the "hadi" provider)
def get_hadi_token() -> str:
    return get_provider_token("hadi")

def get_hadi_url() -> str:
    return get_provider_url("hadi")

def get_hadi_interval() -> int:
    return get_provider_interval("hadi")

# ================= BALANCE & EARNING HELPERS =================
# Default per-service earning rate (TK) — used as fallback when no DB override exists.
SERVICE_RATES = {
    "facebook":  EARN_PER_OTP,
    "instagram": EARN_PER_OTP,
    "newfb":     EARN_PER_OTP,
    "whatsapp":  EARN_PER_OTP,
    "telegram":  EARN_PER_OTP,
    "discord":   0.40}

# Canonical list of services shown in the admin "Rate" panel.
# newfb শেয়ার করে instagram এর rate।
RATE_SERVICES = ["facebook", "instagram", "whatsapp", "telegram", "discord"]

# ── Custom (manually added) service rate support ──────────────────────
# RATE_SERVICES উপরে fixed/known service গুলার জন্য। কিন্তু admin Stock Upload
# এর সময় "Custom Service (Type Name)" দিয়ে যেকোনো নতুন service (e.g. Netflix,
# PayPal) যোগ করতে পারে — এই ফাংশনটা DB থেকে সেই সব custom service নাম বের
# করে আনে, যাতে Rate প্যানেলেও সেগুলার rate up/down করা যায়।
async def get_custom_rate_services() -> list:
    """Return custom earning services from stock OR the persistent registry."""
    known_keys = {_rate_key(s) for s in RATE_SERVICES}
    custom = []
    seen_keys = set()
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                """SELECT service_name FROM earning_services
                   WHERE enabled = 1
                   UNION
                   SELECT DISTINCT n.service FROM numbers n
                   WHERE n.service IS NOT NULL AND TRIM(n.service) != ''
                     AND NOT EXISTS (
                         SELECT 1 FROM earning_services e
                         WHERE LOWER(TRIM(e.service_name)) = LOWER(TRIM(n.service))
                           AND e.enabled = 0
                     )"""
            ) as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            svc = (row[0] or "").strip()
            if not svc:
                continue
            key = _rate_key(svc)
            if key in known_keys or key in seen_keys:
                continue
            seen_keys.add(key)
            custom.append(svc)
    except Exception:
        pass
    return sorted(custom, key=lambda s: s.lower())

def _rate_key(svc: str) -> str:
    svc_key = svc.lower()
    if svc_key == "newfb":
        svc_key = "instagram"
    return svc_key

def get_usd_to_bdt_rate() -> float:
    """Admin-configurable profile conversion rate (default 125)."""
    try:
        value = float(state.hadi_settings.get("usd_to_bdt_rate", "125"))
        return value if value > 0 else 125.0
    except (TypeError, ValueError):
        return 125.0

def usd_to_bdt(amount_usd: float) -> float:
    """Legacy conversion helper; OTP rates must never be entered through this."""
    return round(float(amount_usd) * get_usd_to_bdt_rate(), 2)

def bdt_to_usd(amount_bdt: float) -> float:
    return float(amount_bdt) / get_usd_to_bdt_rate()

def format_dual_rate(amount_bdt: float) -> str:
    """OTP earning rates are always displayed in Bangladeshi taka only."""
    return f"৳{float(amount_bdt or 0):.2f}"

def format_balance_dual(amount_bdt: float) -> str:
    """Non-profile balance displays are shown in taka only."""
    return f"{float(amount_bdt or 0):.2f} TK"

async def set_usd_to_bdt_rate(rate: float):
    """Update only the profile's display conversion; OTP earnings stay in BDT."""
    rate = float(rate)
    if rate <= 0:
        raise ValueError("Exchange rate must be greater than zero")
    value = f"{rate:.6f}".rstrip("0").rstrip(".")
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            ("usd_to_bdt_rate", value)
        )
        await db.commit()
    state.hadi_settings["usd_to_bdt_rate"] = value

def get_service_rate(svc: str) -> float:
    """সার্ভিস অনুযায়ী earning rate রিটার্ন করে (admin panel থেকে সেট করা DB value অগ্রাধিকার পায়)।"""
    svc_key = _rate_key(svc)
    db_val = state.hadi_settings.get(f"rate_{svc_key}")
    if db_val is not None:
        try:
            return float(db_val)
        except (TypeError, ValueError):
            pass
    rate = SERVICE_RATES.get(svc_key, EARN_PER_OTP)
    return rate if rate > 0 else EARN_PER_OTP

def earns_money(svc: str) -> bool:
    """rate > 0 হলেই সেই সার্ভিসের জন্য ব্যবহারকারী ব্যালেন্স পাবেন।"""
    return get_service_rate(svc) > 0

async def set_service_rate(svc: str, rate: float):
    """Admin panel থেকে rate পরিবর্তন করে DB তে persist করে।"""
    svc_key = _rate_key(svc)
    db_key = f"rate_{svc_key}"
    val = f"{rate:.2f}"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (db_key, val)
        )
        if svc_key not in {_rate_key(s) for s in RATE_SERVICES}:
            await db.execute(
                "INSERT OR REPLACE INTO earning_services (service_name, created_at, enabled) VALUES (?, COALESCE((SELECT created_at FROM earning_services WHERE service_name = ?), ?), 1)",
                (svc.strip(), svc.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
        await db.commit()
    state.hadi_settings[db_key] = val

# ── Stock-only earning rate (separate override, only applies to local Stock numbers) ──
# যদি কোনো override সেট করা না থাকে, তাহলে এটা normal get_service_rate() এর value ফেরত দেয়
# (অর্থাৎ override না দেওয়া পর্যন্ত behaviour আগের মতোই থাকবে)।
def get_stock_rate(svc: str) -> float:
    """Compatibility helper: stock numbers use the same single service rate."""
    return get_service_rate(svc)

def _country_rate_key(svc: str, country: str) -> str:
    return f"country_rate_{_rate_key(svc)}_{_rate_key(str(country or 'Global'))}"

def _global_country_rate_key(country: str) -> str:
    return f"global_country_rate_{_rate_key(str(country or 'Global'))}"

def _read_rate_setting(key: str):
    db_val = state.hadi_settings.get(key)
    if db_val is None:
        return None
    try:
        return float(db_val)
    except (TypeError, ValueError):
        return None

def get_country_rate(svc: str, country: str):
    """Return service+country override, or None when not set."""
    return _read_rate_setting(_country_rate_key(svc, country))

def get_global_country_rate(country: str):
    """Return global country override, shared by every service, or None."""
    return _read_rate_setting(_global_country_rate_key(country))

async def set_country_rate(svc: str, country: str, rate: float):
    key = _country_rate_key(svc, country)
    val = f"{rate:.2f}"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, val))
        await db.commit()
    state.hadi_settings[key] = val

async def set_global_country_rate(country: str, rate: float):
    key = _global_country_rate_key(country)
    val = f"{rate:.2f}"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, val))
        await db.commit()
    state.hadi_settings[key] = val

def get_effective_earning_rate(svc: str, country: str = None, stock_only: bool = False) -> float:
    """Priority: service+country override -> global country override -> service/stock rate."""
    if country:
        cr = get_country_rate(svc, country)
        if cr is not None:
            return cr
        gr = get_global_country_rate(country)
        if gr is not None:
            return gr
    return get_service_rate(svc)

def has_stock_rate_override(svc: str) -> bool:
    """এই সার্ভিসের জন্য স্টক-অনলি rate override সেট করা আছে কিনা।"""
    svc_key = _rate_key(svc)
    return state.hadi_settings.get(f"stock_rate_{svc_key}") is not None

def stock_earns_money(svc: str) -> bool:
    """rate > 0 হলেই স্টক নাম্বারের জন্য ব্যবহারকারী ব্যালেন্স পাবেন।"""
    return get_stock_rate(svc) > 0

async def set_stock_rate(svc: str, rate: float):
    """Admin panel থেকে স্টক-অনলি rate পরিবর্তন করে DB তে persist করে।"""
    svc_key = _rate_key(svc)
    db_key = f"stock_rate_{svc_key}"
    val = f"{rate:.2f}"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (db_key, val))
        await db.commit()
    state.hadi_settings[db_key] = val

async def clear_stock_rate(svc: str):
    """স্টক-অনলি rate override মুছে দিয়ে normal rate তে ফিরিয়ে দেয়।"""
    svc_key = _rate_key(svc)
    db_key = f"stock_rate_{svc_key}"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM bot_settings WHERE key = ?", (db_key,))
        await db.commit()
    state.hadi_settings.pop(db_key, None)

DEFAULT_MIN_WITHDRAW = 50.00

def get_min_withdraw() -> float:
    """বর্তমান minimum withdrawal amount রিটার্ন করে (admin panel থেকে সেট করা হলে সেটাই)।"""
    db_val = state.hadi_settings.get("min_withdraw")
    if db_val is not None:
        try:
            return float(db_val)
        except (TypeError, ValueError):
            pass
    return DEFAULT_MIN_WITHDRAW

async def set_min_withdraw(amount: float):
    """Admin panel থেকে minimum withdrawal amount পরিবর্তন করে DB তে persist করে।"""
    val = f"{amount:.2f}"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", ("min_withdraw", val))
        await db.commit()
    state.hadi_settings["min_withdraw"] = val


def is_withdraw_enabled() -> bool:
    """Admin-controlled master switch for user withdrawals."""
    return str(state.hadi_settings.get("withdraw_enabled", "1")).strip().lower() in {
        "1", "true", "on", "yes"
    }


async def set_withdraw_enabled(enabled: bool):
    val = "1" if enabled else "0"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            ("withdraw_enabled", val),
        )
        await db.commit()
    state.hadi_settings["withdraw_enabled"] = val


def is_country_code_enabled(user_id: int | None = None) -> bool:
    """Return country-code display preference for one user only.

    The old implementation stored a single global value, so one user's
    ADD/REMOVE action changed the setting for everyone.  User-specific values
    are stored under country_code_enabled_user_<telegram_id>; the global
    country_code_enabled value remains only as the default for users who have
    never changed the button.
    """
    if user_id is not None:
        key = f"country_code_enabled_user_{int(user_id)}"
        if key in state.hadi_settings:
            return str(state.hadi_settings.get(key, "1")).strip().lower() in {"1", "true", "on", "yes"}
    return str(state.hadi_settings.get("country_code_enabled", "1")).strip().lower() in {"1", "true", "on", "yes"}

async def set_country_code_enabled(enabled: bool, user_id: int | None = None):
    val = "1" if enabled else "0"
    key = f"country_code_enabled_user_{int(user_id)}" if user_id is not None else "country_code_enabled"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, val))
        await db.commit()
    state.hadi_settings[key] = val

def is_channel_stock_alert_enabled() -> bool:
    return str(state.hadi_settings.get("channel_stock_alert", "1")).lower() in {"1", "true", "on", "yes"}

async def set_channel_stock_alert_enabled(enabled: bool):
    val = "1" if enabled else "0"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", ("channel_stock_alert", val))
        await db.commit()
    state.hadi_settings["channel_stock_alert"] = val

def is_bot_stock_alert_enabled() -> bool:
    return str(state.hadi_settings.get("bot_stock_alert", "1")).lower() in {"1", "true", "on", "yes"}

async def set_bot_stock_alert_enabled(enabled: bool):
    val = "1" if enabled else "0"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", ("bot_stock_alert", val))
        await db.commit()
    state.hadi_settings["bot_stock_alert"] = val

async def get_balance(user_id: int) -> float:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance FROM balances WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0.0

async def add_balance(user_id: int, amount: float):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO balances (user_id, balance) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?",
            (user_id, amount, amount)
        )
        await db.commit()

async def record_user_otp(user_id: int, service: str, earned: float = 0.0):
    """প্রতি received OTP-এর service-wise count ও earned amount persist করে।"""
    if not user_id:
        return
    svc = str(service or "Unknown").strip() or "Unknown"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO user_otp_stats (user_id, service, otp_count, total_earned) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(user_id, service) DO UPDATE SET "
            "otp_count = otp_count + 1, total_earned = total_earned + ?",
            (user_id, svc, float(earned or 0.0), float(earned or 0.0))
        )
        today = bd_today()
        await db.execute(
            "INSERT INTO user_daily_otp_stats (user_id, otp_date, otp_count) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, otp_date) DO UPDATE SET otp_count = otp_count + 1",
            (user_id, today)
        )
        await db.commit()

async def record_panel_otp(panel_key: str):
    """Persist one OTP received from a specific panel, using Bangladesh date."""
    key = str(panel_key or "unknown").strip() or "unknown"
    today = bd_today()
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO panel_daily_otp_stats (panel_key, otp_date, otp_count) VALUES (?, ?, 1) "
            "ON CONFLICT(panel_key, otp_date) DO UPDATE SET otp_count = otp_count + 1",
            (key, today),
        )
        await db.commit()


async def get_panel_otp_counts(panel_key: str) -> tuple[int, int]:
    """Return (today_count, lifetime_count) for one panel."""
    today = bd_today()
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(otp_count), 0), "
            "COALESCE(SUM(CASE WHEN otp_date = ? THEN otp_count ELSE 0 END), 0) "
            "FROM panel_daily_otp_stats WHERE panel_key = ?",
            (today, str(panel_key or "unknown")),
        ) as cur:
            lifetime, today_count = await cur.fetchone()
    return int(today_count or 0), int(lifetime or 0)


async def get_today_otp_count(user_id: int) -> int:
    # A new Bangladesh date means TODAY OTP starts from 0 automatically after midnight.
    today = bd_today()
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT otp_count FROM user_daily_otp_stats WHERE user_id = ? AND otp_date = ?",
            (user_id, today)
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] or 0) if row else 0

async def get_user_otp_stats(user_id: int) -> dict:
    """User-er service-wise OTP count ফেরত দেয়: {service: {count, earned}}."""
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT service, otp_count, total_earned FROM user_otp_stats WHERE user_id = ? ORDER BY service COLLATE NOCASE",
            (user_id,)
        ) as cur:
            rows = await cur.fetchall()
    return {str(r[0]): {"count": int(r[1] or 0), "earned": float(r[2] or 0.0)} for r in rows}

async def deduct_balance(user_id: int, amount: float) -> bool:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance FROM balances WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            current = row[0] if row else 0.0
        if current < amount:
            return False
        await db.execute("UPDATE balances SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
        return True

# ================= REFER & EARN SYSTEM =================
def get_referral_commission_rate() -> float:
    """Admin-configured lifetime referral commission rate, stored as decimal."""
    try:
        value = float(state.hadi_settings.get("referral_commission_rate", "0.05"))
        return min(1.0, max(0.0, value))
    except (TypeError, ValueError):
        return 0.05


def get_referral_bonus_per_refer() -> float:
    """Admin-configured one-time bonus for each successfully registered referral."""
    try:
        value = float(state.hadi_settings.get("referral_bonus_per_refer", "1.00"))
        return max(0.0, value)
    except (TypeError, ValueError):
        return 1.00


async def set_referral_setting(key: str, value: float):
    if key not in {"referral_commission_rate", "referral_bonus_per_refer"}:
        raise ValueError("Unsupported referral setting")
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, str(value)))
        await db.commit()
    state.hadi_settings[key] = str(value)

async def get_referrer(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT referrer_id FROM referrals WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_referrer(user_id: int, referrer_id: int) -> bool:
    """Register one successful referral and credit the configured one-time bonus once."""
    if not referrer_id or referrer_id == user_id:
        return False

    bonus = get_referral_bonus_per_refer()
    now = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT referrer_id FROM referrals WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            if await cursor.fetchone():
                await db.rollback()
                return False

        await db.execute(
            "INSERT INTO referrals (user_id, referrer_id, joined_at) VALUES (?, ?, ?)",
            (user_id, referrer_id, now)
        )
        await db.execute(
            "INSERT INTO referral_earnings (referrer_id, from_user_id, amount, timestamp) VALUES (?, ?, ?, ?)",
            (referrer_id, user_id, bonus, now)
        )
        await db.execute(
            "INSERT INTO balances (user_id, balance) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?",
            (referrer_id, bonus, bonus)
        )
        await db.commit()

    # This bonus is credited only after the referred user successfully completes
    # the bot's verification/new-user flow. Existing commission remains separate.
    return True

async def count_referrals(referrer_id: int) -> int:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (referrer_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_referral_total_earned(referrer_id: int) -> float:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT SUM(amount) FROM referral_earnings WHERE referrer_id = ?", (referrer_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else 0.0


async def credit_referral_commission(referred_uid: int, earned_amount: float):
    """Credit the admin-configured percentage of each referred user's earning."""
    if earned_amount <= 0:
        return
    referrer_id = await get_referrer(referred_uid)
    if not referrer_id:
        return
    commission = round(earned_amount * get_referral_commission_rate(), 2)
    if commission <= 0:
        return
    await add_balance(referrer_id, commission)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO referral_earnings (referrer_id, from_user_id, amount, timestamp) VALUES (?, ?, ?, ?)",
            (referrer_id, referred_uid, commission, datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()
    if False:  # Referral commission notifications disabled as requested.
        try:
            await bot.send_message(
                referrer_id,
                f"{ce(E_ADMIN_CASH, '💰')} <b>Referral bonus:</b> <code>{commission:.2f} TK</code>"
            )
        except Exception:
            pass

# ================= COUNTRIES LIST =================
COUNTRIES = {
    '1': ('USA/Canada',             E_FLAG_US),
    '7': ('Russia/Kazakhstan',      E_FLAG_RU),
    '20': ('Egypt',                 E_FLAG_EG),
    '211': ('South Sudan',          E_FLAG_SS),
    '212': ('Morocco',              E_FLAG_MA),
    '213': ('Algeria',              E_FLAG_DZ),
    '216': ('Tunisia',              E_FLAG_TN),
    '218': ('Libya',                E_FLAG_LY),
    '220': ('Gambia',               E_FLAG_GM),
    '221': ('Senegal',              E_FLAG_SN),
    '222': ('Mauritania',           E_FLAG_MR),
    '223': ('Mali',                 E_FLAG_ML),
    '224': ('Guinea',               E_FLAG_GN),
    '225': ('Ivory Coast',          E_FLAG_CI),
    '226': ('Burkina Faso',         E_FLAG_BF),
    '227': ('Niger',                E_FLAG_NE),
    '228': ('Togo',                 E_FLAG_TG),
    '229': ('Benin',                E_FLAG_BJ),
    '230': ('Mauritius',            E_FLAG_MU),
    '231': ('Liberia',              E_FLAG_LR),
    '232': ('Sierra Leone',         E_FLAG_SL),
    '233': ('Ghana',                E_FLAG_GH),
    '234': ('Nigeria',              E_FLAG_NG),
    '235': ('Chad',                 E_FLAG_TD),
    '236': ('Central African Rep.', E_FLAG_CF),
    '237': ('Cameroon',             E_FLAG_CM),
    '238': ('Cape Verde',           E_FLAG_CV),
    '239': ('Sao Tome & Principe',  E_FLAG_ST),
    '240': ('Equatorial Guinea',    E_FLAG_GQ),
    '241': ('Gabon',                E_FLAG_GA),
    '242': ('Congo',                E_FLAG_CG),
    '243': ('DR Congo',             E_FLAG_CD),
    '244': ('Angola',               E_FLAG_AO),
    '245': ('Guinea-Bissau',        E_FLAG_GW),
    '248': ('Seychelles',           E_FLAG_SC),
    '249': ('Sudan',                E_FLAG_SD),
    '250': ('Rwanda',               E_FLAG_RW),
    '251': ('Ethiopia',             E_FLAG_ET),
    '252': ('Somalia',              E_FLAG_SO),
    '253': ('Djibouti',             E_FLAG_DJ),
    '254': ('Kenya',                E_FLAG_KE),
    '255': ('Tanzania',             E_FLAG_TZ),
    '256': ('Uganda',               E_FLAG_UG),
    '257': ('Burundi',              E_FLAG_BI),
    '258': ('Mozambique',           E_FLAG_MZ),
    '260': ('Zambia',               E_FLAG_ZM),
    '261': ('Madagascar',           E_FLAG_MG),
    '263': ('Zimbabwe',             E_FLAG_ZW),
    '264': ('Namibia',              E_FLAG_NA),
    '265': ('Malawi',               E_FLAG_MW),
    '266': ('Lesotho',              E_FLAG_LS),
    '267': ('Botswana',             E_FLAG_BW),
    '268': ('Eswatini',             E_FLAG_SZ),
    '269': ('Comoros',              E_FLAG_KM),
    '27':  ('South Africa',         E_FLAG_ZA),
    '30':  ('Greece',               E_FLAG_GR),
    '31':  ('Netherlands',          E_FLAG_NL),
    '32':  ('Belgium',              E_FLAG_BE),
    '33':  ('France',               E_FLAG_FR),
    '34':  ('Spain',                E_FLAG_ES),
    '350': ('Gibraltar',            E_FLAG_GI),
    '351': ('Portugal',             E_FLAG_PT),
    '352': ('LUxembourg',           E_FLAG_LU),
    '353': ('Ireland',              E_FLAG_IE),
    '354': ('Iceland',              E_FLAG_IS),
    '355': ('Albania',              E_FLAG_AL),
    '356': ('Malta',                E_FLAG_MT),
    '357': ('Cyprus',               E_FLAG_CY),
    '358': ('Finland',              E_FLAG_FI),
    '359': ('Bulgaria',             E_FLAG_BG),
    '370': ('Lithuania',            E_FLAG_LT),
    '371': ('Latvia',               E_FLAG_LV),
    '372': ('Estonia',              E_FLAG_EE),
    '373': ('Moldova',              E_FLAG_MD),
    '374': ('Armenia',              E_FLAG_AM),
    '375': ('Belarus',              E_FLAG_BY),
    '376': ('Andorra',              E_FLAG_AD),
    '377': ('Monaco',               E_FLAG_MC),
    '378': ('San Marino',           E_FLAG_SM),
    '380': ('Ukraine',              E_FLAG_UA),
    '381': ('Serbia',               E_FLAG_RS),
    '382': ('Montenegro',           E_FLAG_ME),
    '385': ('Croatia',              E_FLAG_HR),
    '386': ('Slovenia',             E_FLAG_SI),
    '387': ('Bosnia',               E_FLAG_BA),
    '389': ('North Macedonia',      E_FLAG_MK),
    '39':  ('Italy',                E_FLAG_IT),
    '40':  ('Romania',              E_FLAG_RO),
    '41':  ('Switzerland',          E_FLAG_CH),
    '420': ('Czech Republic',       E_FLAG_CZ),
    '421': ('Slovakia',             E_FLAG_SK),
    '423': ('Liechtenstein',        E_FLAG_LI),
    '43':  ('Austria',              E_FLAG_AT),
    '44':  ('United Kingdom',       E_FLAG_GB),
    '45':  ('Denmark',              E_FLAG_DK),
    '46':  ('Sweden',               E_FLAG_SE),
    '47':  ('Norway',               E_FLAG_NO),
    '48':  ('Poland',               E_FLAG_PL),
    '49':  ('Germany',              E_FLAG_DE),
    '501': ('Belize',               E_FLAG_BZ),
    '502': ('Guatemala',            E_FLAG_GT),
    '503': ('El Salvador',          E_FLAG_SV),
    '504': ('Honduras',             E_FLAG_HN),
    '505': ('Nicaragua',            E_FLAG_NI),
    '506': ('Costa Rica',           E_FLAG_CR),
    '507': ('Panama',               E_FLAG_PA),
    '509': ('Haiti',                E_FLAG_HT),
    '51':  ('Peru',                 E_FLAG_PE),
    '52':  ('Mexico',               E_FLAG_MX),
    '53':  ('Cuba',                 E_FLAG_CU),
    '54':  ('Argentina',            E_FLAG_AR),
    '55':  ('Brazil',               E_FLAG_BR),
    '56':  ('Chile',                E_FLAG_CL),
    '57':  ('Colombia',             E_FLAG_CO),
    '58':  ('Venezuela',            E_FLAG_VE),
    '591': ('Bolivia',              E_FLAG_BO),
    '592': ('Guyana',               E_FLAG_GY),
    '593': ('Ecuador',              E_FLAG_EC),
    '595': ('Paraguay',             E_FLAG_PY),
    '597': ('Suriname',             E_FLAG_SR),
    '598': ('Uruguay',              E_FLAG_UY),
    '60':  ('Malaysia',             E_FLAG_MY),
    '61':  ('Australia',            E_FLAG_AU),
    '62':  ('Indonesia',            E_FLAG_ID),
    '63':  ('Philippines',          E_FLAG_PH),
    '64':  ('New Zealand',          E_FLAG_NZ),
    '65':  ('Singapore',            E_FLAG_SG),
    '66':  ('Thailand',             E_FLAG_TH),
    '673': ('Brunei',               E_FLAG_BN),
    '675': ('Papua New Guinea',     E_FLAG_PG),
    '679': ('Fiji',                 E_FLAG_FJ),
    '81':  ('Japan',                E_FLAG_JP),
    '82':  ('South Korea',          E_FLAG_KR),
    '84':  ('Vietnam',              E_FLAG_VN),
    '852': ('Hong Kong',            E_FLAG_HK),
    '855': ('Cambodia',             E_FLAG_KH),
    '856': ('Laos',                 E_FLAG_LA),
    '86':  ('China',                E_FLAG_CN),
    '880': ('Bangladesh',           E_FLAG_BD),
    '886': ('Taiwan',               E_FLAG_TW),
    '90':  ('Turkey',               E_FLAG_TR),
    '91':  ('India',                E_FLAG_IN),
    '92':  ('Pakistan',             E_FLAG_PK),
    '93':  ('Afghanistan',          E_FLAG_AF),
    '94':  ('Sri Lanka',            E_FLAG_LK),
    '95':  ('Myanmar',              E_FLAG_MM),
    '960': ('Maldives',             E_FLAG_MV),
    '961': ('Lebanon',              E_FLAG_LB),
    '962': ('Jordan',               E_FLAG_JO),
    '963': ('Syria',                E_FLAG_SY),
    '964': ('Iraq',                 E_FLAG_IQ),
    '965': ('Kuwait',               E_FLAG_KW),
    '966': ('Saudi Arabia',         E_FLAG_SA),
    '967': ('Yemen',                E_FLAG_YE),
    '968': ('Oman',                 E_FLAG_OM),
    '970': ('Palestine',            E_FLAG_PS),
    '971': ('UAE',                  E_FLAG_AE),
    '972': ('Israel',               E_FLAG_IL),
    '973': ('Bahrain',              E_FLAG_BH),
    '974': ('Qatar',                E_FLAG_QA),
    '975': ('Bhutan',               E_FLAG_BT),
    '976': ('Mongolia',             E_FLAG_MN),
    '977': ('Nepal',                E_FLAG_NP),
    '98':  ('Iran',                 E_FLAG_IR),
    '992': ('Tajikistan',           E_FLAG_TJ),
    '993': ('Turkmenistan',         E_FLAG_TM),
    '994': ('Azerbaijan',           E_FLAG_AZ),
    '995': ('Georgia',              E_FLAG_GE),
    '996': ('Kyrgyzstan',           E_FLAG_KG),
    '998': ('Uzbekistan',           E_FLAG_UZ)}

_prefix_to_short = {
    '1':   'US', '7':   'RU',
    '20':  'EG', '27':  'ZA', '30':  'GR', '31':  'NL',
    '32':  'BE', '33':  'FR', '34':  'ES', '36':  'HU',
    '39':  'IT', '40':  'RO', '41':  'CH', '43':  'AT',
    '44':  'GB', '45':  'DK', '46':  'SE', '47':  'NO',
    '48':  'PL', '49':  'DE',
    '51':  'PE', '52':  'MX', '53':  'CU', '54':  'AR',
    '55':  'BR', '56':  'CL', '57':  'CO', '58':  'VE',
    '60':  'MY', '61':  'AU', '62':  'ID', '63':  'PH',
    '64':  'NZ', '65':  'SG', '66':  'TH',
    '76':  'KZ', '77':  'KZ',
    '81':  'JP', '82':  'KR', '84':  'VN', '86':  'CN',
    '90':  'TR', '91':  'IN', '92':  'PK', '93':  'AF',
    '94':  'LK', '95':  'MM', '98':  'IR',
    '211': 'SS', '212': 'MA', '213': 'DZ', '216': 'TN',
    '218': 'LY', '220': 'GM', '221': 'SN', '222': 'MR',
    '223': 'ML', '224': 'GN', '225': 'CI', '226': 'BF',
    '227': 'NE', '228': 'TG', '229': 'BJ', '230': 'MU',
    '231': 'LR', '232': 'SL', '233': 'GH', '234': 'NG',
    '235': 'TD', '236': 'CF', '237': 'CM', '238': 'CV',
    '239': 'ST', '240': 'GQ', '241': 'GA', '242': 'CG',
    '243': 'CD', '244': 'AO', '245': 'GW', '248': 'SC',
    '249': 'SD', '250': 'RW', '251': 'ET', '252': 'SO',
    '253': 'DJ', '254': 'KE', '255': 'TZ', '256': 'UG',
    '257': 'BI', '258': 'MZ', '260': 'ZM', '261': 'MG',
    '263': 'ZW', '264': 'NA', '265': 'MW', '266': 'LS',
    '267': 'BW', '268': 'SZ', '269': 'KM',
    '350': 'GI', '351': 'PT', '352': 'LU', '353': 'IE',
    '354': 'IS', '355': 'AL', '356': 'MT', '357': 'CY',
    '358': 'FI', '359': 'BG', '370': 'LT', '371': 'LV',
    '372': 'EE', '373': 'MD', '374': 'AM', '375': 'BY',
    '376': 'AD', '377': 'MC', '378': 'SM', '380': 'UA',
    '381': 'RS', '382': 'ME', '385': 'HR', '386': 'SI',
    '387': 'BA', '389': 'MK',
    '501': 'BZ', '502': 'GT', '503': 'SV', '504': 'HN',
    '505': 'NI', '506': 'CR', '507': 'PA', '509': 'HT',
    '591': 'BO', '592': 'GY', '593': 'EC', '595': 'PY',
    '597': 'SR', '598': 'UY',
    '673': 'BN', '675': 'PG', '679': 'FJ',
    '852': 'HK', '855': 'KH', '856': 'LA',
    '880': 'BD', '886': 'TW',
    '960': 'MV', '961': 'LB', '962': 'JO', '963': 'SY',
    '964': 'IQ', '965': 'KW', '966': 'SA', '967': 'YE',
    '968': 'OM', '970': 'PS', '971': 'AE', '972': 'IL',
    '973': 'BH', '974': 'QA', '975': 'BT', '976': 'MN',
    '977': 'NP', '992': 'TJ', '993': 'TM', '994': 'AZ',
    '995': 'GE', '996': 'KG', '998': 'UZ'}

def clean_number(raw) -> str:
    if raw is None:
        return ""
    digits = re.sub(r'\D', '', str(raw))
    if len(digits) < 7:
        return ""
    return digits



def unicode_flag(iso_code: str) -> str:
    """Return a normal Unicode country flag (no Telegram Premium emoji)."""
    code = (iso_code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return "🏳️"
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)


def country_flag_emoji(country_name: str) -> str:
    """Map a country name to standard Unicode flag emoji."""
    target = (country_name or "").strip().lower()
    for prefix, (full_name, _flag_id) in COUNTRIES.items():
        if full_name.split('/')[0].strip().lower() == target:
            if prefix == "1":
                return "🇺🇸🇨🇦"
            if prefix == "7":
                return "🇷🇺🇰🇿"
            code = _prefix_to_short.get(prefix)
            return unicode_flag(code)
    return "🏳️"

def get_flag_id_by_country(country_name: str) -> str:
    for code, (full_name, flag_id) in COUNTRIES.items():
        if full_name.split('/')[0].strip().lower() == country_name.lower():
            return flag_id
    return E_FLAG_GLOBAL

def get_country_emoji(country_name: str) -> str:
    return country_flag_emoji(country_name)

def get_svc_icon(svc: str):
    """Return the raw Premium/custom emoji ID for a service.
    Admin-selected service_emoji_map has priority for custom/typed services,
    followed by the built-in service emoji map and finally the default icon.
    """
    key = svc.strip().lower()
    mapped_key = state.service_emoji_map.get(key)
    if mapped_key:
        return _emoji_id_for_key(mapped_key)
    return _emoji_id_for_key(key)

@lru_cache(maxsize=512)
def _parse_country_cached(num_str: str):
    s = re.sub(r'\D', '', num_str)
    for l in [4, 3, 2, 1]:
        if s[:l] in COUNTRIES:
            return s[:l]
    return None

def get_country(num):
    key = _parse_country_cached(re.sub(r'\D', '', str(num)))
    if key:
        name, flag_id = COUNTRIES[key]
        return f"{ce(flag_id, '🏳')} {name}"
    return f"{ce(E_FLAG_GLOBAL, '🏳️')} Global"

def get_country_parts(num):
    key = _parse_country_cached(re.sub(r'\D', '', str(num)))
    if key:
        name, flag_id = COUNTRIES[key]
        short = _prefix_to_short.get(key, name.split('/')[0][:2].upper())
        return ce(flag_id, '🏳'), short
    return ce(E_FLAG_GLOBAL, '🏳️'), "GL"

def country_name_for_number(num: str) -> str:
    """Return the detected country for one uploaded number.

    Unknown calling prefixes are kept as Global instead of rejecting the
    entire file, so a mixed-country upload can still be started.
    """
    key = _parse_country_cached(re.sub(r'\D', '', str(num)))
    if key:
        return COUNTRIES[key][0].split('/')[0].strip()
    return "Global"

FORWARD_POOL_LANGUAGE_LABELS = {
    "en": "English",
    "bn": "বাংলা",
    "hi": "हिन्दी",
    "ar": "العربية",
    "ur": "اردو"}

FORWARD_POOL_TRANSLATIONS = {
    "en": {"service": "Service", "country": "Country", "number": "Number", "otp": "OTP", "message": "Message"},
    "bn": {"service": "সার্ভিস", "country": "দেশ", "number": "নম্বর", "otp": "ওটিপি", "message": "মেসেজ"},
    "hi": {"service": "सेवा", "country": "देश", "number": "नंबर", "otp": "ओटीपी", "message": "संदेश"},
    "ar": {"service": "الخدمة", "country": "الدولة", "number": "الرقم", "otp": "رمز OTP", "message": "الرسالة"},
    "ur": {"service": "سروس", "country": "ملک", "number": "نمبر", "otp": "OTP", "message": "پیغام"}}

def forward_pool_language_keyboard(selected: list[str] | None = None) -> dict:
    selected_set = set(selected or [])
    rows = []
    for code, label in FORWARD_POOL_LANGUAGE_LABELS.items():
        mark = "✅ " if code in selected_set else ""
        rows.append([btn(f"{mark}{label}", E_FWD_LANG_TAG, callback_data=f"fpool_lang_{code}", style="danger" if code in selected_set else "danger")])
    rows.append([btn("Done", E_FWD_OK, callback_data="fpool_lang_done", style="danger")])
    rows.append([btn("Cancel", E_BROADCAST_FAIL, callback_data="admin_panel", style="danger")])
    return markup(*rows)

def forward_mask_number(number: str) -> str:
    """Show the international prefix and last four digits, masking the middle."""
    digits = re.sub(r"\D", "", str(number or ""))
    if len(digits) > 9:
        return "+" + digits[:5] + "xxxx" + digits[-4:]
    return "+" + digits if digits else "+Unknown"


async def send_dynamic_otp_card(target_chat, number: str, country: str, service: str, otp: str, caption: str, reply_markup: dict):
    """Forward OTP as a compact text card, without the old screenshot-style image.

    The visible message uses the phone-number line and service/country line;
    OTP copy and Channel/Get Number controls remain in the inline keyboard.
    """
    await bot.send_message(
        chat_id=target_chat,
        text=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )

def forward_pool_text(number: str, country: str, service: str, message: str, otp: str, languages: list[str]) -> str:
    """Build the same masked OTP card as the regular forwarder.

    One selected language is used per card. The generator rotates through the
    admin-selected language list between cards so a card never contains a
    stack of repeated translations.
    """
    country_name = str(country or country_name_for_number(number) or "Unknown")
    country_flag = country_flag_emoji(country_name)
    fwd_icon = fwd_svc_icon(service)
    service_label = html.escape(svc_display_name(service))
    return (
        f"☎️ <b>{html.escape(forward_mask_number(number))}</b>\n"
        f"{fwd_icon} <b>{service_label}</b> · {country_flag} <b>{html.escape(country_name)}</b>"
    )

FORWARD_POOL_GENERATED_MESSAGES = {
    "en": "💬 # {otp} is your {service} code\n{otp}",
    "bn": "💬 # {otp} আপনার {service} কোড\n{otp}",
    "hi": "💬 # {otp} आपका {service} कोड है\n{otp}",
    "ar": "💬 # {otp} هو رمز {service}\n{otp}",
    "ur": "💬 # {otp} آپ کا {service} کوڈ ہے\n{otp}"}

def generated_forward_pool_message(service: str, otp: str, languages: list[str]) -> str:
    selected = [code for code in languages if code in FORWARD_POOL_GENERATED_MESSAGES] or ["en"]
    # forward_pool_text() escapes the complete provider/generated message once
    # before placing it into HTML. Keep the service name raw here to avoid
    # displaying double-escaped entities for custom names.
    display_service = svc_display_name(service)
    return FORWARD_POOL_GENERATED_MESSAGES[selected[0]].format(service=display_service, otp=otp)

def group_ranges_by_country(ranges_list: list[str]) -> dict[str, list[str]]:
    """Phone ranges কে country name অনুযায়ী group করে — 'Select a Country' menu বানাতে।"""
    grouped = {}
    for r in ranges_list:
        s = re.sub(r'\D', '', str(r))
        cname = None
        for l in [4, 3, 2, 1]:
            if s[:l] in COUNTRIES:
                full_name, _ = COUNTRIES[s[:l]]
                cname = full_name.split('/')[0].strip()
                break
        if not cname:
            cname = "Global"
        grouped.setdefault(cname, []).append(r)
    return grouped

def strip_cc(num):
    """Strip the country calling-code prefix from a number, leaving only the local digits."""
    digits = re.sub(r'\D', '', str(num))
    key = _parse_country_cached(digits)
    if key:
        return digits[len(key):]
    return digits

def hide_number(number):
    digits_only = re.sub(r'\D', '', str(number))
    if len(digits_only) > 6:
        return digits_only[:3] + '****' + digits_only[-3:]
    return digits_only


def detect_language(sms_text: str) -> str:
    if not sms_text:
        return "English"
    # Beshi specific/unique script age check hoy, jate kono overlap na hoy
    # (jemon Mongolian-er Ө/Ү Cyrillic block-er modhee thakleo age check
    # kore Russian-er age dhora hoy).
    if re.search(r'[\u0900-\u097F]', sms_text):
        return "Hindi"          # India / Nepal (Devanagari)
    if re.search(r'[\u3040-\u309F\u30A0-\u30FF]', sms_text):
        return "Japanese"       # Japan (Hiragana/Katakana)
    if re.search(r'[\uAC00-\uD7A3]', sms_text):
        return "Korean"         # South Korea (Hangul)
    if re.search(r'[\u0E00-\u0E7F]', sms_text):
        return "Thai"           # Thailand
    if re.search(r'[\u1780-\u17FF]', sms_text):
        return "Khmer"          # Cambodia
    if re.search(r'[\u0E80-\u0EFF]', sms_text):
        return "Lao"            # Laos
    if re.search(r'[\u1000-\u109F]', sms_text):
        return "Burmese"        # Myanmar
    if re.search(r'[\u0D80-\u0DFF]', sms_text):
        return "Sinhala"        # Sri Lanka
    if re.search(r'[\u0590-\u05FF]', sms_text):
        return "Hebrew"         # Israel
    if re.search(r'[\u0370-\u03FF]', sms_text):
        return "Greek"          # Greece
    if re.search(r'[\u0530-\u058F]', sms_text):
        return "Armenian"       # Armenia
    if re.search(r'[\u10A0-\u10FF]', sms_text):
        return "Georgian"       # Georgia
    if re.search(r'[\u1200-\u137F]', sms_text):
        return "Amharic"        # Ethiopia
    if re.search(r'[ӨөҮүᠮ]', sms_text):
        return "Mongolian"      # Mongolia (Cyrillic-extra letters, checked before Russian)
    if re.search(r'[\u0600-\u06FF]', sms_text):
        return "Arabic"         # Egypt/Middle East/Iran/Afghanistan/Pakistan etc. (Arabic script)
    if re.search(r'[\u0400-\u04FF]', sms_text):
        return "Russian"        # Russia/Kazakhstan/Ukraine/Belarus etc. (Cyrillic)
    if re.search(r'[\u4e00-\u9fff]', sms_text):
        return "Chinese"        # China/Taiwan/Hong Kong
    if re.search(r'[\u0980-\u09FF]', sms_text):
        return "Bengali"        # Bangladesh
    # ---- African (Latin script + language-specific letters) ----
    # Yoruba er ẹ/ọ Vietnamese er shathe EXACT shei Unicode codepoint share
    # kore, tai shudhu ṣ (S with dot above, Vietnamese e nei) diye Yoruba
    # detect kora hocche.
    if re.search(r'[ṣṢ]', sms_text):
        return "Yoruba"         # Nigeria
    if re.search(r'[ƙɓɗƴƘƁƊƳ]', sms_text):
        return "Hausa"          # Nigeria / Niger / West Africa
    # Vietnamese er ã/õ er moto shared Latin-1 char o thakte pare (jemon
    # Portuguese er shathe), tai eita European checks er AGE boshano
    # hoyeche — Vietnamese er nijer unique đ/ơ/ư ba tone-mark range takei
    # age priority deya hocche.
    if re.search(r'[đơưĐƠƯ]', sms_text) or re.search(r'[\u1EA0-\u1EF9]', sms_text):
        return "Vietnamese"     # Vietnam (Latin script + unique diacritics)
    # ---- European (Latin script, unique-char based since letters overlap English) ----
    if re.search(r'[ñÑ¿¡]', sms_text):
        return "Spanish"        # Spain / Latin America
    if re.search(r'[ãõÃÕ]', sms_text):
        return "Portuguese"     # Portugal / Brazil / Angola / Mozambique
    if re.search(r'[œŒçÇàâèêëîïôùûÿÀÂÈÊËÎÏÔÙÛŸ]', sms_text):
        return "French"         # France / Francophone West & Central Africa
    if re.search(r'[ßẞäöüÄÖÜ]', sms_text):
        return "German"         # Germany / Austria / Switzerland
    if re.search(r'[ğışĞİŞ]', sms_text):
        return "Turkish"        # Turkey
    # Note: Swahili, Zulu, Xhosa, Somali plain Latin script e likhe (kono
    # unique diacritic nei), tai English theke reliably alada kora jay na —
    # tai eigulo detect kora hoyni, English hishebei dhora hobe.
    return "English"

# ================= USER JOIN CHECK =================
async def is_joined(user_id: int) -> bool:
    if not force_join_enabled() or not state.force_join_channels:
        return True

    async def _check(chat_id):
        try:
            member = await asyncio.wait_for(
                bot.get_chat_member(chat_id, user_id),
                timeout=5.0
            )
            return member.status not in ("left", "kicked")
        except asyncio.TimeoutError:
            print(f"[is_joined] Timeout checking {chat_id}")
            return False
        except Exception:
            return False

    results = await asyncio.gather(*[_check(ch["chat_id"]) for ch in state.force_join_channels.values()])
    return all(results)

def join_verify_keyboard(channel_keys=None) -> dict:
    """Show only the join button(s) for the required chat(s) the user needs.

    If channel_keys is provided, show only those specific chats (used when a
    chat_member update tells us exactly which channel/group the user left).
    """
    selected = set(channel_keys) if channel_keys is not None else None
    rows = [
        [btn(f"Join {ch['label']}", E_JOIN_CHANNEL, url=ch["url"], style="danger")]
        for key, ch in state.force_join_channels.items()
        if selected is None or key in selected
    ]
    return markup(*rows)


async def missing_force_join_keys(user_id: int):
    """Return configured force-join keys the user has not joined."""
    missing = []
    for key, ch in state.force_join_channels.items():
        try:
            member = await asyncio.wait_for(bot.get_chat_member(ch["chat_id"], user_id), timeout=5.0)
            if member.status in ("left", "kicked"):
                missing.append(key)
        except Exception:
            # If Telegram cannot verify membership, keep that target visible.
            missing.append(key)
    return missing

# ================= TELEGRAM CLIENTS =================
# Must be initialized before any @dp.message / @dp.callback_query decorators.
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
BOT_USERNAME = ""   # filled in main() via bot.get_me()
dp = Dispatcher()

# ================= INSTANT FORCE-JOIN LEAVE DETECTION =================
def _force_join_chat_matches(update: types.ChatMemberUpdated, configured_chat_id: str) -> bool:
    """Match membership updates using numeric Telegram chat IDs only."""
    configured = str(configured_chat_id).strip()
    if configured.lstrip("-").isdigit():
        return str(update.chat.id) == configured
    if configured.startswith("@"):
        return bool(update.chat.username) and ("@" + update.chat.username).lower() == configured.lower()
    return str(update.chat.id) == configured


@dp.chat_member()
async def force_join_member_update(update: types.ChatMemberUpdated):
    """Telegram sends this update when a user joins/leaves a force-join chat.
    A leave immediately invalidates verification, so the user cannot keep using
    the bot after exiting a required channel/group."""
    if not force_join_enabled() or not state.force_join_channels:
        return

    # from_user is the actor who changed membership; the actual affected user
    # is new_chat_member.user (these can differ when an admin adds someone).
    target_user = update.new_chat_member.user
    uid = target_user.id
    if target_user.is_bot:
        return

    matched_keys = [
        key for key, ch in state.force_join_channels.items()
        if _force_join_chat_matches(update, ch["chat_id"])
    ]
    if not matched_keys:
        return

    new_member = update.new_chat_member
    new_status = getattr(new_member, "status", "")
    is_member_flag = getattr(new_member, "is_member", None)
    has_left = new_status in ("left", "kicked") or (
        new_status == "restricted" and is_member_flag is False
    )
    if has_left:
        state.verified_users.discard(uid)
        state.rk_visible.discard(uid)
        try:
            # Remove the previous prompt first, then persist the new prompt's ID
            # so it can be deleted when the user rejoins all required chats.
            await _delete_persisted_force_join_message(uid)
            prompt = await bot.send_message(
                uid,
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Force Join required 👇👇</b>",
                reply_markup=join_verify_keyboard(matched_keys),
            )
            state.force_join_messages[uid] = prompt.message_id
            await _save_force_join_message_id(uid, prompt.message_id)
        except Exception as e:
            # User may have blocked the bot; verification is still invalidated.
            print(f"[ForceJoin] Could not notify {uid}: {e}")
        return

    # If the user joined/rejoined, only restore access after ALL required chats
    # pass the same authoritative getChatMember check used by Verify Now.
    if new_status in ("member", "administrator", "creator") or (
        new_status == "restricted" and is_member_flag is True
    ):
        if await is_joined(uid):
            state.verified_users.add(uid)
            is_new = add_user(users_db, target_user, uid)
            await _finalize_referral_if_new(uid, is_new)
            state.rk_visible.add(uid)
            # All required channels are now joined: remove the old force-join
            # message so its Join buttons do not remain visible in the chat.
            await _delete_persisted_force_join_message(uid)
            try:
                await send_with_reply_kb(uid, main_menu_text(target_user), uid)
            except Exception as e:
                print(f"[ForceJoin] Could not restore menu for {uid}: {e}")

# ================= REPLY KEYBOARDS =================

@dp.message(F.text.func(lambda value: isinstance(value, str) and value.strip() in {"Profile", "👤 Profile"}), F.chat.type == "private")
async def rk_profile(m: types.Message):
    uid = m.from_user.id

    # Re-check verification here so the Profile button still works after a
    # restart, even if the in-memory verified_users set is temporarily empty.
    if uid not in state.verified_users:
        if force_join_enabled() and state.force_join_channels:
            if not await is_joined(uid):
                await m.answer(
                    f"{ce(E_BROADCAST_FAIL, '❌')} <b>Force Join required 👇👇</b>",
                    reply_markup=join_verify_keyboard(await missing_force_join_keys(uid)),
                )
                return
        state.verified_users.add(uid)
        state.rk_visible.add(uid)

    # Remove the previous button message, but keep this newly pressed button message.
    await _track_reply_button_message(m)
    # Switching away from Get Number: remove the active service/number panel
    # and release any numbers/orders owned by this user.
    await _clear_previous_service_flow(uid)
    await _release_other_stock_services(m.chat.id)
    for phone, info in list(state.active_orders.items()):
        if info.get("chat_id") == m.chat.id:
            state.active_orders.pop(phone, None)
    await show_user_profile(m)


def profile_keyboard(user_id: int) -> dict:
    # Profile: Withdraw first, Referral directly underneath; no Back button.
    # Tapping Referral copies the user's referral link directly to the clipboard.
    ref_link = (
        f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        if BOT_USERNAME else "Bot username unavailable"
    )
    return markup(
        [btn("💸 Withdraw", E_ADMIN_CASH, callback_data="profile_withdraw", style="danger")],
        [btn("REFER LINK", E_FWD_OK, copy_text=ref_link, style="danger")],
    )


async def _user_service_otp_block(user_id: int) -> str:
    """Profile-e configured service rate + oi service-er received OTP count দেখায়।"""
    stats = await get_user_otp_stats(user_id)
    try:
        custom_services = await get_custom_rate_services()
    except Exception:
        custom_services = []

    services = []
    seen = set()
    for svc in list(RATE_SERVICES) + list(custom_services):
        key = _rate_key(svc)
        if key in seen:
            continue
        seen.add(key)
        rate = get_service_rate(svc)
        # আগের requirement অনুযায়ী 0 TK service profile-e দেখাব না।
        if rate <= 0:
            continue
        services.append((svc, rate, stats.get(svc, {}).get("count", 0)))

    # Stats-e এমন custom service থাকতে পারে যেটা এখন আর numbers table-e নেই;
    # rate সেট থাকলে সেটাও profile-e রাখি।
    for svc, item in stats.items():
        key = _rate_key(svc)
        if key in seen:
            continue
        rate = get_service_rate(svc)
        if rate > 0:
            services.append((svc, rate, item.get("count", 0)))
            seen.add(key)

    if not services:
        return ""

    lines = ["📲 <b>OTP & Service Rate</b>"]
    for svc, rate, count in services:
        lines.append(f"• <b>{html.escape(svc)}</b> — <b>{format_dual_rate(rate)}/OTP</b> · Received: <b>{count}</b>")
    return "\n".join(lines) + "\n"

async def get_user_otp_period_counts(user_id: int):
    """Return today's, last 7 days', last 30 days and all-time OTP counts."""
    today = bd_today()
    today_date = datetime.now(BD_TZ).date()
    start_7 = (today_date - timedelta(days=6)).strftime("%Y-%m-%d")
    start_30 = (today_date - timedelta(days=29)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(otp_count), 0) FROM user_daily_otp_stats WHERE user_id = ? AND otp_date = ?",
            (user_id, today),
        ) as cur:
            today_count = int((await cur.fetchone())[0] or 0)
        async with db.execute(
            "SELECT COALESCE(SUM(otp_count), 0) FROM user_daily_otp_stats WHERE user_id = ? AND otp_date BETWEEN ? AND ?",
            (user_id, start_7, today),
        ) as cur:
            seven_day = int((await cur.fetchone())[0] or 0)
        async with db.execute(
            "SELECT COALESCE(SUM(otp_count), 0) FROM user_daily_otp_stats WHERE user_id = ? AND otp_date BETWEEN ? AND ?",
            (user_id, start_30, today),
        ) as cur:
            thirty_day = int((await cur.fetchone())[0] or 0)
        async with db.execute(
            "SELECT COALESCE(SUM(otp_count), 0) FROM user_daily_otp_stats WHERE user_id = ?",
            (user_id,),
        ) as cur:
            all_time = int((await cur.fetchone())[0] or 0)
    return today_count, seven_day, thirty_day, all_time


def _profile_display_name(user) -> str:
    """Build a safe Telegram display name for the user's profile card."""
    name = " ".join(filter(None, [
        str(getattr(user, "first_name", "") or "").strip(),
        str(getattr(user, "last_name", "") or "").strip(),
    ])).strip()
    if not name:
        username = str(getattr(user, "username", "") or "").strip()
        name = f"@{username}" if username else "User"
    return html.escape(name)


def _format_profile_balance(balance_bdt: float) -> str:
    """Show taka balance and its approximate dollar equivalent on the profile only."""
    balance = float(balance_bdt or 0)
    dollar_equivalent = bdt_to_usd(balance)
    return f"৳{balance:.2f} = ${dollar_equivalent:.2f}"


async def _build_user_profile_text(user_id: int) -> str:
    balance, ref_count, otp_periods = await asyncio.gather(
        get_balance(user_id), count_referrals(user_id), get_user_otp_period_counts(user_id)
    )
    today_otp, seven_day_otp, _thirty_day_otp, all_time_otp = otp_periods
    return (
        "<b>👤 PROFILE</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"<b>💰 BALANCE: {_format_profile_balance(balance)}</b>\n"
        f"<b>💱 RATE: ৳{get_usd_to_bdt_rate():g} = $1.00</b>\n"
        "<b>🏦 BINANCE WITHDRAW: USD ($) ONLY</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"<b>            💬 TODAY OTP : {today_otp}</b>\n"
        f"<b>            💬 7 DAY OTP   : {seven_day_otp}</b>\n"
        f"<b>            💬 TOTAL OTP  : {all_time_otp}</b>\n"
        f"<b>            👥 ALL REFER   : {ref_count}</b>"
    )


async def show_user_profile(m: types.Message):
    uid = m.from_user.id
    text = await _build_user_profile_text(uid)
    sent = await m.answer(text, reply_markup=profile_keyboard(uid), parse_mode="HTML")
    _remember_service_flow(uid, sent, "profile")



@dp.callback_query(F.data == "profile_balance")
async def profile_balance(c: types.CallbackQuery):
    uid = c.from_user.id
    balance = await get_balance(uid)
    await c.answer()
    await safe_edit(
        c.message,
        "💰 <b>YOUR BALANCE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💵 AVAILABLE BALANCE: <b>{_format_profile_balance(balance)}</b>\n"
        f"💵 MINIMUM WITHDRAWAL: <b>{format_balance_dual(get_min_withdraw())}</b>",
        markup(
            [btn("💸 Withdraw", E_ADMIN_CASH, callback_data="profile_withdraw", style="danger")],
            [btn("↩️ Back to Profile", E_TOOL_BACKBUTTON, callback_data="profile_back", style="danger")],
        )
    )


@dp.callback_query(F.data == "profile_referral")
async def profile_referral(c: types.CallbackQuery):
    uid = c.from_user.id
    ref_count, total_earned, service_block = await asyncio.gather(
        count_referrals(uid),
        get_referral_total_earned(uid),
        _user_service_otp_block(uid),
    )
    ref_link = (
        f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
        if BOT_USERNAME else "Bot username unavailable"
    )
    text = (
        "👥 <b>REFER & EARN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{service_block}"
        f"👥 REFERRALS: <b>{ref_count}</b>\n"
        f"💰 TOTAL EARNED: <b>{total_earned:.2f} TK</b>\n"
        f"📈 COMMISSION: <b>{get_referral_commission_rate() * 100:.2f}%</b>\n"
        f"🎁 NEW REFERRAL BONUS: <b>{get_referral_bonus_per_refer():.2f} TK</b>\n\n"
        "🔗 YOUR REFERRAL LINK:\n"
        f"<code>{html.escape(ref_link)}</code>"
    )
    await c.answer()
    await safe_edit(
        c.message, text,
        markup(
            [btn("📤 Share Link", E_BROADCAST, url=f"https://t.me/share/url?url={ref_link}", style="danger")],
            [btn("↩️ Back to Profile", E_TOOL_BACKBUTTON, callback_data="profile_back", style="danger")],
        )
    )


@dp.callback_query(F.data == "profile_back")
async def profile_back(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    text = await _build_user_profile_text(uid)
    await safe_edit(c.message, text, profile_keyboard(uid))


@dp.callback_query(F.data == "profile_withdraw")
async def profile_withdraw(c: types.CallbackQuery):
    # Profile -> Withdraw uses the exact same payment-method-first flow
    # as the main Withdraw Balance button.
    uid = c.from_user.id
    if not is_withdraw_enabled():
        await c.answer("⚠️ Withdraw Disabled Right now", show_alert=True)
        return
    bal = await get_balance(uid)
    min_wd = get_min_withdraw()
    if bal < min_wd:
        await c.answer(f"⚠️ Minimum withdrawal is {min_wd:.2f} TK!", show_alert=True)
        return

    state.pending_withdrawal_amount.pop(uid, None)
    state.pending_withdrawal_details.pop(uid, None)
    state.pending_withdrawal_method[uid] = True

    otp_stats = await get_user_otp_stats(uid)
    total_otp = sum(int(item.get("count", 0)) for item in otp_stats.values())
    daily_otp = await get_today_otp_count(uid)
    text = (
        "《 😔 WITHDRAWAL 》\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 <b>TOTAL OTP:</b> <code>{total_otp}</code>\n"
        f"📅 <b>TODAY OTP:</b> <code>{daily_otp}</code>\n"
        f"💰 <b>BALANCE:</b> <code>{format_balance_dual(bal)}</code>\n"
        f"🔐 <b>MINIMUM WITHDRAW:</b> <code>{min_wd:.2f} TK</code>\n\n"
        "<b>SELECT PAYMENT METHOD:</b>"
    )
    kb = markup(
        [btn("NAGAD", callback_data="withdraw_opt_nagad", style="danger"),
         btn("BKASH", E_WD_BKASH, callback_data="withdraw_opt_bkash", style="danger")],
        [btn("BINANCE UID", E_WD_BINANCE, callback_data="withdraw_opt_binance", style="danger")],
        [btn("↩️ Back", E_BROADCAST_FAIL, callback_data="profile_back", style="danger")]
    )
    await safe_edit(c.message, text, kb)


def get_support_username() -> str:
    """Return the support username configured from Admin Panel, or empty when disabled."""
    value = str(state.hadi_settings.get("support_username", "")).strip()
    if not value:
        return ""
    return value.lstrip("@")

async def set_support_username(username: str | None):
    value = (str(username).strip() if username is not None else "").lstrip("@")
    async with aiosqlite.connect(DB_FILE) as db:
        if value:
            await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", ("support_username", value))
        else:
            await db.execute("DELETE FROM bot_settings WHERE key = ?", ("support_username",))
        await db.commit()
    if value:
        state.hadi_settings["support_username"] = value
    else:
        state.hadi_settings.pop("support_username", None)


def main_reply_keyboard(user_id: int = 0) -> dict:
    # Telegram Bot API reply-keyboard buttons support native styles:
    # danger = red, success = green, primary = blue.
    # Do NOT send icon_custom_emoji_id here; that was the source of the
    # previous TelegramBadRequest on some deployments.
    rows = [
        [
            {"text": "📞 Get Number", "style": "danger"},
            {"text": "👤 Profile"},
        ],
    ]
    if get_support_username():
        rows.append([{"text": "💬 Support"}])
    if is_admin(user_id):
        # Telegram's official Bot API currently has no native yellow/warning
        # button style. Keep this valid with primary/blue and the yellow gear.
        rows.append([{"text": "⚙️ Admin Panel"}])
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "input_field_placeholder": "Select an option…",
    }

async def send_with_reply_kb(chat_id: int, text: str, user_id: int = 0):
    await _tg_client.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": main_reply_keyboard(user_id)}
    )


def main_menu_text(user) -> str:
    """Build the NS SMS BOT welcome message."""
    return (
        "✴️ WELCOME to NS SMS BOT ✴️\n"
        "👑 Owner ➤ SHOAIB 🐯"
    )

async def admin_stats_text() -> str:
    """Build the Admin Panel header with Today/7 Day/30 Day/Lifetime OTP counts."""
    check_daily_reset()
    today = bd_today()
    start_7 = (datetime.now(BD_TZ).date() - timedelta(days=6)).strftime("%Y-%m-%d")
    start_30 = (datetime.now(BD_TZ).date() - timedelta(days=29)).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(otp_count), 0) FROM user_daily_otp_stats WHERE otp_date BETWEEN ? AND ?",
            (start_7, today),
        ) as cur:
            otp_7_day = int((await cur.fetchone())[0] or 0)

        async with db.execute(
            "SELECT COALESCE(SUM(otp_count), 0) FROM user_daily_otp_stats WHERE otp_date BETWEEN ? AND ?",
            (start_30, today),
        ) as cur:
            otp_30_day = int((await cur.fetchone())[0] or 0)

        async with db.execute(
            "SELECT COALESCE(SUM(otp_count), 0) FROM user_daily_otp_stats"
        ) as cur:
            otp_lifetime = int((await cur.fetchone())[0] or 0)

    # Keep the admin header text and line separators exactly in the requested layout.
    return (
        "⚙️ NS SMS BOT — ADMIN PANEL\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 TODAY OTP: {state.daily_otp_count}\n"
        f"💬 7 DAY OTP: {otp_7_day}\n"
        f"💬 30 DAY OTP: {otp_30_day}\n"
        f"💬 LIFETIME OTP: {otp_lifetime}"
    )


# ================= DEMO OTP GENERATOR =================
async def _demo_uploaded_options():
    """Return uploaded service/country/prefix combinations only.

    If the admin selected multiple DEMO services/countries, only those selected
    entries are returned. Empty selection means all currently uploaded entries.
    Stock numbers are used only as metadata; they are never sent or consumed.
    """
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """SELECT service, country, phone_number
               FROM numbers
               WHERE service IS NOT NULL AND TRIM(service) != ''
                 AND country IS NOT NULL AND TRIM(country) != ''
                 AND phone_number IS NOT NULL AND TRIM(phone_number) != ''"""
        ) as cursor:
            rows = await cursor.fetchall()

    selected_pairs = {
        (str(service).strip(), str(country).strip())
        for service, country in getattr(state, 'demo_otp_pairs', set())
        if str(service).strip() and str(country).strip()
    }
    options, seen = [], set()
    for service, country, source_number in rows:
        service = str(service).strip(); country = str(country).strip()
        if selected_pairs and (service, country) not in selected_pairs:
            continue
        digits = re.sub(r"\D", "", str(source_number))
        cc = _parse_country_cached(digits) if digits else None
        if not cc:
            continue
        key = (service, country, cc)
        if key not in seen:
            seen.add(key); options.append(key)
    return options


async def _demo_uploaded_catalog():
    """Return unique uploaded services and countries for the DEMO selector."""
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """SELECT service, country FROM numbers
               WHERE service IS NOT NULL AND TRIM(service) != ''
                 AND country IS NOT NULL AND TRIM(country) != ''"""
        ) as cursor:
            rows = await cursor.fetchall()
    # IMPORTANT: DEMO selector is built ONLY from the current uploaded stock.
    # Do not use registered/configured services or hard-coded service names here.
    services = sorted({str(a).strip() for a,b in rows if str(a).strip()}, key=str.lower)
    countries = sorted({str(b).strip() for a,b in rows if str(b).strip()}, key=str.lower)

    # Drop stale selections automatically if stock was removed after a previous selection.
    uploaded_services = set(services)
    uploaded_countries = set(countries)
    state.demo_otp_services.intersection_update(uploaded_services)
    state.demo_otp_countries.intersection_update(uploaded_countries)
    return services, countries


async def _demo_uploaded_pairs():
    """Return unique Service/Country pairs that actually exist in uploaded stock."""
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """SELECT service, country FROM numbers
               WHERE service IS NOT NULL AND TRIM(service) != ''
                 AND country IS NOT NULL AND TRIM(country) != ''"""
        ) as cursor:
            rows = await cursor.fetchall()
    return sorted({(str(a).strip(), str(b).strip()) for a, b in rows
                   if str(a).strip() and str(b).strip()},
                  key=lambda x: (x[0].lower(), x[1].lower()))


def _demo_number(cc: str) -> str:
    """Create a synthetic number: country-code + 2 digits + 4 digits.
    The four Xs are presentation-only masking, not a real stock number.
    """
    return f"{cc}{secrets.randbelow(90) + 10}{secrets.randbelow(10000):04d}"


def _demo_masked_number(cc: str, local2: str, last4: str) -> str:
    # Keep the middle of the demo number masked with x characters.
    return f"+{cc}{local2}xxxx{last4}"


def _demo_card_text(service: str, country: str, display_number: str) -> str:
    """Demo card deliberately contains NO OTP label/value in the message body."""
    # DEMO card uses a standard Unicode country flag so it is visible on
    # every Telegram client (do not rely on Premium/custom emoji IDs).
    flag_emoji = country_flag_emoji(country)
    if flag_emoji == "🏳️":
        digits = re.sub(r"\D", "", display_number)
        flag_emoji, _short = get_country_parts(digits)
        # get_country_parts may return a custom emoji; keep a visible fallback.
        if not flag_emoji:
            flag_emoji = "🏳️"
    return (
        f"☎️ <b>{html.escape(display_number)}</b>\n"
        f"{svc_tag_icon(service)} <b>{html.escape(svc_display_name(service))}</b> · "
        f"{flag_emoji} <b>{html.escape(country)}</b>"
    )


def _demo_keyboard(otp: str, service: str = None, country: str = None) -> dict:
    # Keep the same layout as every other OTP group card:
    # copy OTP on the first row, then Channel + Get Number side by side.
    rows = [[btn(bold_button(str(otp)), E_OTP_KEY, copy_text=str(otp), style="danger")]]
    rows.append([
        btn(bold_button("Channel"), E_FWD_BELL, url=FWD_CHANNEL_URL, style="danger"),
        btn(bold_button("Get Number"), E_RK_GET_NUM, url=FWD_GET_NUMBER_URL, style="__status_red__"),
    ])
    return markup(*rows)


async def demo_otp_monitor():
    """Send one synthetic DEMO OTP at the configured interval.

    Service/country choices come exclusively from currently uploaded stock
    metadata. The interval is configured by the admin and persisted in
    bot_settings.
    """
    tag = "[DEMO OTP]"
    print(f"{tag} generator started.")
    while state.demo_otp_enabled:
        try:
            options = await _demo_uploaded_options()
            if not options:
                print(f"{tag} no uploaded service/country available; waiting 10s.")
                state.demo_otp_next_at = time.time() + 10
                await asyncio.sleep(10)
                continue

            service, country, cc = secrets.choice(options)
            local2 = f"{secrets.randbelow(90) + 10:02d}"
            last4 = f"{secrets.randbelow(10000):04d}"
            otp = f"{secrets.randbelow(1000000):06d}"
            display_number = _demo_masked_number(cc, local2, last4)
            card_text = _demo_card_text(service, country, display_number)

            demo_target = OTP_GROUP_ID
            try:
                await bot.send_message(
                    chat_id=demo_target,
                    text=card_text,
                    parse_mode="HTML",
                    reply_markup=_demo_keyboard(otp, service, country),
                )
                print(f"{tag} sent to {demo_target} | next in {state.demo_otp_interval}s")
            except Exception as send_err:
                print(f"{tag} send error to {demo_target}: {send_err}")

            selected_pairs = {
                (str(s).strip(), str(c).strip())
                for s, c in getattr(state, "demo_otp_pairs", set())
                if str(s).strip() and str(c).strip()
            }
            current_pair = (str(service).strip(), str(country).strip())

            if selected_pairs:
                # Selected Service/Country pairs use the admin-set interval.
                if current_pair in selected_pairs:
                    delay = max(1, min(int(state.demo_otp_interval or 30), 3600))
                else:
                    # Non-selected uploaded pairs use a random interval.
                    delay = secrets.randbelow(
                        max(1, int(getattr(state, "demo_otp_random_max", 60))
                            - int(getattr(state, "demo_otp_random_min", 15)) + 1)
                    ) + int(getattr(state, "demo_otp_random_min", 15))
            else:
                # Nothing selected: all uploaded pairs use random timing.
                delay = secrets.randbelow(
                    max(1, int(getattr(state, "demo_otp_random_max", 60))
                        - int(getattr(state, "demo_otp_random_min", 15)) + 1)
                ) + int(getattr(state, "demo_otp_random_min", 15))

            state.demo_otp_next_at = time.time() + delay
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            print(f"{tag} cancelled.")
            return
        except Exception as err:
            print(f"{tag} error: {err}")
            await asyncio.sleep(5)


def demo_otp_admin_keyboard() -> dict:
    running = bool(state.demo_otp_task and not state.demo_otp_task.done() and state.demo_otp_enabled)
    return markup(
        [btn("Service", E_DEMO_OTP, suppress_auto_emoji=True,
             callback_data="adm_demo_otp_select", style="danger"),
         btn("Start" if not running else "Stop", E_DEMO_OTP, suppress_auto_emoji=True,
             callback_data="adm_demo_otp_start" if not running else "adm_demo_otp_stop",
             style=None if running else "__status_red__")],
        [btn("Refresh", E_TOOL_REFRESHING, suppress_auto_emoji=True,
             callback_data="adm_demo_otp", style="danger")],
        [btn("Back", E_TOOL_BACKBUTTON, suppress_auto_emoji=True,
             callback_data="admin_panel", style="danger")],
    )


def demo_otp_admin_text() -> str:
    """Compact DEMO OTP screen: only the next-OTP countdown is shown."""
    running = bool(state.demo_otp_task and not state.demo_otp_task.done() and state.demo_otp_enabled)
    if running and state.demo_otp_next_at:
        remaining = max(0, int(state.demo_otp_next_at - time.time()))
        countdown = f"{remaining} seconds"
    else:
        countdown = "Stopped"
    interval = max(1, min(int(getattr(state, "demo_otp_interval", 30) or 30), 3600))
    return (
        f"⏳ <b>Next OTP: {html.escape(countdown)}</b>\n"
        f"⏱ <b>Selected: {interval} seconds</b>\n"
        f"🎲 <b>Unselected: random {getattr(state, 'demo_otp_random_min', 15)}–{getattr(state, 'demo_otp_random_max', 60)} seconds</b>"
    )


def demo_otp_selector_keyboard(pairs) -> dict:
    """Show only actually uploaded service/country pairs, then Select."""
    rows = []
    pairs = sorted({(str(service).strip(), str(country).strip())
                    for service, country in pairs
                    if str(service).strip() and str(country).strip()},
                   key=lambda x: (x[0].lower(), x[1].lower()))
    for i, (service, country) in enumerate(pairs):
        selected_pairs = {
            (str(s).strip(), str(c).strip())
            for s, c in getattr(state, "demo_otp_pairs", set())
        }
        selected = (service, country) in selected_pairs
        label = ("✅ " if selected else "⬜ ") + f"{service} — {country}"
        rows.append([btn(label, E_DEMO_OTP, callback_data=f"adm_demo_pair_{i}",
                         style="danger" if selected else "danger")])
    interval = max(1, min(int(getattr(state, "demo_otp_interval", 30) or 30), 3600))
    rows.append([
        btn(f"{interval}s", E_TOOL_REFRESHING, suppress_auto_emoji=True,
            callback_data="adm_demo_interval", style="danger"),
        btn("Select", E_DEMO_OTP, suppress_auto_emoji=True,
            callback_data="adm_demo_select_confirm", style="danger"),
    ])
    rows.append([btn("Back", E_TOOL_BACKBUTTON, suppress_auto_emoji=True,
                     callback_data="adm_demo_otp", style="danger")])
    return markup(*rows)


def admin_keyboard() -> dict:
    # Admin menu: one medium-length button per row, aligned under the header.
    return markup(
        [btn("📁 UPLOAD STOCK", E_RK_GET_NUM, callback_data="adm_upload_stock", style="danger")],
        [btn("DELETE STOCK", E_ADMIN_WRENCH, callback_data="adm_delete_stock", style="danger")],
        [btn("X", E_DEMO_OTP, callback_data="adm_demo_otp", style="danger")],
        [btn("BOT SETTINGS", E_ADMIN_WRENCH, callback_data="adm_settings", style="danger")],
        [btn("PANEL", E_ADMIN_TOOL, callback_data="adm_panel_mgmt", style="danger")],
        [btn("USER MANAGEMENT", E_ADMIN_USERS, callback_data="adm_users", style="danger")],
        [btn("👑 ADMIN MANAGEMENT", E_ADMIN_USERS, callback_data="adm_co_admins", style="danger")],
        [btn("📣 BROADCAST", E_BROADCAST, callback_data="adm_broadcast_menu", style="danger")],
    )


def admin_keyboard_for(user_id: int) -> dict:
    """Vertical admin menu with readable, medium-length buttons."""
    rows = [
        [btn("📁 UPLOAD STOCK", E_RK_GET_NUM, callback_data="adm_upload_stock", style="danger")],
        [btn("DELETE STOCK", E_ADMIN_WRENCH, callback_data="adm_delete_stock", style="danger")],
        [btn("X", E_DEMO_OTP, callback_data="adm_demo_otp", style="danger")],
        [btn("BOT SETTINGS", E_ADMIN_WRENCH, callback_data="adm_settings", style="danger")],
        [btn("PANEL", E_ADMIN_TOOL, callback_data="adm_panel_mgmt", style="danger")],
        [btn("USER MANAGEMENT", E_ADMIN_USERS, callback_data="adm_users", style="danger")],
    ]
    if user_id == ADMIN_ID:
        rows.append([btn("👑 ADMIN MANAGEMENT", E_ADMIN_USERS, callback_data="adm_co_admins", style="danger")])
    rows.append([btn("📣 BROADCAST", E_BROADCAST, callback_data="adm_broadcast_menu", style="danger")])
    return markup(*rows)


def admin_cat_overview_keyboard() -> dict:
    return markup(
        [btn("Stats",           E_ADMIN_DATE, callback_data="adm_stats",    style="danger")],
        [btn("↩️ Back",            E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")],
    )

def admin_cat_control_keyboard() -> dict:
    # Bot Control submenu. Keep these callbacks aligned with the handlers
    # already present in this file.
    return markup(
        [btn("Panel Management", E_ADMIN_TOOL, callback_data="adm_panel_mgmt", style="danger")],
        [btn("Force join", E_JOIN_CHANNEL, callback_data="adm_forcejoin_mgmt", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")],
    )


def admin_cat_users_keyboard(user_id: int | None = None) -> dict:
    # Admin Management is intentionally visible only to the main/super admin.
    # Co-admins can manage users, but they cannot see or open the admin-management screen.
    rows = [
        [btn("Users", E_ADMIN_USERS, callback_data="adm_users", style="danger")],
    ]
    if user_id == ADMIN_ID:
        rows.append([btn("👑 Admin Management", E_ADMIN_USERS, callback_data="adm_co_admins", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
    return markup(*rows)

def admin_cat_earnings_keyboard() -> dict:
    return markup(
        [btn("Rate",   E_ADMIN_CASH, callback_data="adm_rates",          style="danger"),
         btn("Broadcast",       E_BROADCAST,  callback_data="adm_broadcast_menu", style="danger")],
        [btn("↩️ Back",            E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")],
    )

def admin_cat_stock_keyboard() -> dict:
    return markup(
        [btn("Upload Stock",    E_RK_GET_NUM,   callback_data="adm_upload_stock",    style="danger"),
         btn("Number Forward Pool", E_ADMIN_BOLT, callback_data="adm_forward_pool_upload", style="danger")],
        [btn("Active Forward Pools", E_ADMIN_OTP, callback_data="adm_forward_pool_list", style="danger")],
        [btn("DELETE STOCK", E_ADMIN_WRENCH, callback_data="adm_delete_stock", style="danger"),
         btn("X", E_DEMO_OTP, callback_data="adm_demo_otp", style="danger")],
        [btn("Download Unused", E_ADMIN_BOLT,   callback_data="adm_download_unused", style="danger"),
         btn("Backup users.json", E_ADMIN_USERS, callback_data="adm_backup_users",   style="danger")],
        [btn("↩️ Back",            E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")],
    )


def admin_stats_keyboard() -> dict:
    return markup(
        [btn("Refresh Stats", E_TOOL_REFRESHING, callback_data="adm_stats",    style="danger")],
        [btn("↩️ Back",          E_TOOL_BACKBUTTON, callback_data="admin_panel",  style="danger")],
    )

def admin_co_admins_text() -> str:
    lines = [f"{ce(E_ADMIN_USERS, '👥')} <b>Admin Management</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
    lines.append(f"{ce(E_FWD_OK, '👑')} Main Admin: <code>{ADMIN_ID}</code>\n")
    if state.co_admin_ids:
        for i, cid in enumerate(sorted(state.co_admin_ids), 1):
            lines.append(f"{i}. <code>{cid}</code>")
    else:
        lines.append("<i>No sub-admins added yet.</i>")
    lines.append(f"\n{ce(E_MENU_PIN, '📌')} <b>{len(state.co_admin_ids)}/{MAX_CO_ADMINS}</b> sub-admin slots used")
    return "\n".join(lines)

def admin_co_admins_keyboard() -> dict:
    rows = []
    if len(state.co_admin_ids) < MAX_CO_ADMINS:
        rows.append([btn("Add Sub Admin", E_FWD_OK, callback_data="adm_addco", style="danger")])
    for cid in sorted(state.co_admin_ids):
        rows.append([btn(f"Remove Sub Admin {cid}", E_BROADCAST_FAIL, callback_data=f"adm_rmco_{cid}", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
    return markup(*rows)

def admin_broadcast_keyboard() -> dict:
    return markup(
        [btn("Send Broadcast",  E_BROADCAST,       callback_data="admin_broadcast",  style="danger")],
        [btn("↩️ Back",            E_TOOL_BACKBUTTON,  callback_data="admin_panel",      style="danger")],
    )

def admin_users_keyboard(page: int = 0, total: int = 0) -> dict:
    # Keep User Management actions compact so the keyboard visually follows
    # the width of the User Management message instead of stretching across
    # the full chat width.
    return markup(
        [btn("ALL USER", E_ADMIN_USERS, callback_data="adm_all_users", style="danger")],
        [btn("🗓️ Daily Leaderboard", E_ADMIN_DATE, callback_data="adm_top10_users", style="danger")],
        [btn("🔗 Rafer Details", E_ADMIN_DATE, callback_data="adm_user_stats", style="danger")],
        [btn("ADD BALANCE", E_ADMIN_CASH, callback_data="adm_add_balance", style="danger")],
        [btn("REMOVE BALANCE", E_ADMIN_CASH, callback_data="adm_remove_balance", style="danger")],
        [btn("DOWNLOAD BALANCE", E_ADMIN_USERS, callback_data="adm_export_balances", style="danger")],
        [btn("UPLOAD BALANCE", E_ADMIN_USERS, callback_data="adm_import_balances", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")],
    )

def admin_settings_keyboard() -> dict:
    # Enabled switches use the default (light/white) button style; disabled = red.
    channel_alert = is_channel_stock_alert_enabled()
    bot_alert = is_bot_stock_alert_enabled()
    withdraw_on = is_withdraw_enabled()
    force_join_on = force_join_enabled()
    withdraw_label = "💸 WITHDRAW: ON" if withdraw_on else "💸 WITHDRAW: OFF"
    rows = [
        [
            btn("CHANNEL STOCK ALERT", E_JOIN_CHANNEL, callback_data="adm_toggle_channel_stock_alert", style=None if channel_alert else "__status_red__"),
            btn("BOT STOCK ALERT", E_RK_GET_NUM, callback_data="adm_toggle_bot_stock_alert", style=None if bot_alert else "__status_red__"),
        ],
        [
            btn("MINIMUM WITHDRAW", E_ADMIN_CASH, callback_data="adm_min_withdraw_direct", style="danger"),
            btn(withdraw_label, E_ADMIN_CASH, callback_data="adm_toggle_withdraw", style=None if withdraw_on else "__status_red__"),
        ],
        [
            btn("🔗 FORCE JOIN", E_JOIN_CHANNEL, callback_data="adm_forcejoin_mgmt", style=None if force_join_on else "__status_red__"),
            btn("SUPPORT MANAGEMENT", E_ADMIN_TOOL, callback_data="adm_support_mgmt", style="danger"),
        ],
        [btn("↩️ BACK", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")],
    ]
    return markup(*rows)

def admin_panel_mgmt_keyboard() -> dict:
    """Compact panel management keyboard."""
    rows = []
    panel_buttons = []
    for provider in SMS_PROVIDERS:
        label = PROVIDER_LABELS.get(provider, provider.capitalize())
        configured = provider_configured(provider)
        dot = '🟢' if configured else '🔴'
        panel_buttons.append(btn(
            f"{dot} {label}",
            E_ADMIN_TOOL,
            callback_data=f"adm_panel_detail_{provider}",
            style="danger" if configured else "danger",
        ))
    # compact two-column layout
    for i in range(0, len(panel_buttons), 2):
        rows.append(panel_buttons[i:i+2])
    rows.append([
        btn("Add Panel", E_FWD_OK, callback_data="adm_add_panel", style="danger"),
    ])
    # Keep the separate login-based panel types accessible from Panel Management.
    rows.append([
        btn("🔐 Auto Captcha Panel", E_MENU_LOCK, callback_data="adm_captcha_mgmt", style="danger"),
    ])
    rows.append([
        btn("🟢 Green SMS Panel", E_MENU_LOCK, callback_data="adm_green_mgmt", style="danger"),
    ])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
    return markup(*rows)

def captcha_panel_mgmt_keyboard() -> dict:
    """Auto Captcha Panel category — separate list from the token+URL panels above.
    Green Panel এর নিজস্ব আলাদা section আছে (green_panel_mgmt_keyboard), তাই এখানে
    শুধু 'generic' টাইপের panel গুলোই দেখানো হয়।"""
    rows = []
    panel_buttons = []
    for key, p in CAPTCHA_PANELS.items():
        if (p.get("panel_type") or "generic") != "generic":
            continue
        label = p.get("label", key)
        ok = p.get("login_status", "").startswith("✅")
        dot = '🟢' if ok else '🔴'
        panel_buttons.append(btn(f"{dot} {label}", E_MENU_LOCK, callback_data=f"adm_cap_detail_{key}", style="danger" if ok else "danger"))
    for i in range(0, len(panel_buttons), 2):
        rows.append(panel_buttons[i:i + 2])
    rows.append([btn("➕ Add Auto Captcha Panel", E_FWD_OK, callback_data="adm_add_cap_panel", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="danger")])
    return markup(*rows)

def captcha_panel_detail_keyboard(key: str) -> dict:
    rows = [
        [btn("🔁 Retry Login", E_TOOL_REFRESHING, callback_data=f"adm_cap_retry_{key}", style="danger")],
        [btn("🧪 Test Connection", E_FWD_OK, callback_data=f"adm_cap_test_{key}", style="danger")],
        [btn("🗑 Remove Panel", E_BROADCAST_FAIL, callback_data=f"adm_rm_cap_panel_{key}", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")],
    ]
    return markup(*rows)

# ── Green Panel (Green SMS) — সম্পূর্ণ আলাদা section, শুধু panel_type=='greennews' ──
def green_panel_mgmt_keyboard() -> dict:
    rows = []
    panel_buttons = []
    for key, p in CAPTCHA_PANELS.items():
        if (p.get("panel_type") or "generic") != "greennews":
            continue
        label = p.get("label", key)
        ok = p.get("login_status", "").startswith("✅")
        dot = '🟢' if ok else '🔴'
        panel_buttons.append(btn(f"{dot} {label}", E_MENU_LOCK, callback_data=f"adm_green_detail_{key}", style="danger" if ok else "danger"))
    for i in range(0, len(panel_buttons), 2):
        rows.append(panel_buttons[i:i + 2])
    rows.append([btn("➕ Add Green Panel", E_FWD_OK, callback_data="adm_add_green_panel", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="danger")])
    return markup(*rows)

def green_panel_detail_keyboard(key: str) -> dict:
    rows = [
        [btn("🔁 Retry Login", E_TOOL_REFRESHING, callback_data=f"adm_green_retry_{key}", style="danger")],
        [btn("🧪 Test Connection", E_FWD_OK, callback_data=f"adm_green_test_{key}", style="danger")],
        [btn("🗑 Remove Panel", E_BROADCAST_FAIL, callback_data=f"adm_rm_green_panel_{key}", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="danger")],
    ]
    return markup(*rows)

def admin_panel_detail_keyboard(provider: str) -> dict:
    label = PROVIDER_LABELS.get(provider, provider.capitalize())
    token_lbl, url_lbl = "Token", "URL"
    rows = [
        [btn(token_lbl, E_OTP_KEY,         callback_data=f"adm_set_{provider}_token",     style="danger")],
        [btn(url_lbl,   E_MENU_GLOBE2,     callback_data=f"adm_set_{provider}_url",       style="danger")],
        [btn("Interval", E_TOOL_REFRESHING, callback_data=f"adm_set_{provider}_interval",  style="danger")],
    ]
    if provider not in BASE_SMS_PROVIDERS:
        rows.append([btn(f"🗑 Remove {label} Panel", E_BROADCAST_FAIL, callback_data=f"adm_rm_panel_{provider}", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="danger")])
    return markup(*rows)

def force_join_mgmt_keyboard() -> dict:
    """Force Join controls with fixed base labels instead of showing usernames."""
    rows = []

    # Always show the configured base targets with fixed labels.
    if "channel" in state.force_join_channels:
        rows.append([btn("CHANNEL", E_JOIN_CHANNEL, callback_data="adm_fj_detail_channel", style="danger")])
    if "otpgroup" in state.force_join_channels:
        rows.append([btn("OTP GROUP", E_JOIN_CHANNEL, callback_data="adm_fj_detail_otpgroup", style="danger")])

    # Additional channels/groups, if added by the admin, keep their configured label.
    extra_buttons = []
    for key, ch in state.force_join_channels.items():
        if key in BASE_FORCE_JOIN_KEYS:
            continue
        label = ch.get("label", key)
        extra_buttons.append(btn(f"📢 {label}", E_JOIN_CHANNEL, callback_data=f"adm_fj_detail_{key}", style="danger"))
    for i in range(0, len(extra_buttons), 2):
        rows.append(extra_buttons[i:i + 2])

    rows.append([btn("➕ Add Channel/Group", E_FWD_OK, callback_data="adm_fj_add", style="danger")])
    enabled = force_join_enabled()
    rows.append([btn(f"Force Join: {'ON' if enabled else 'OFF'}", E_TOOL_REFRESHING, callback_data="adm_fj_toggle", style=None if enabled else "__status_red__")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_settings", style="danger")])
    return markup(*rows)

def force_join_detail_keyboard(key: str) -> dict:
    rows = [[btn("✏️ Edit URL", E_MENU_GLOBE2, callback_data=f"adm_fj_seturl_{key}", style="danger")]]
    if key not in BASE_FORCE_JOIN_KEYS:
        rows.append([btn("🗑 Remove", E_BROADCAST_FAIL, callback_data=f"adm_fj_rm_{key}", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="danger")])
    return markup(*rows)

async def admin_rates_keyboard() -> dict:
    rows = []

    # Only manually added/custom services are shown here.
    # The built-in Facebook/Instagram/WhatsApp/Telegram/Discord entries
    # are intentionally not rendered in this menu.
    # ── Custom (manually added) services ──
    custom_services = await get_custom_rate_services()
    state.custom_rate_svc_list = custom_services
    custom_row = []
    for idx, svc in enumerate(custom_services):
        rate = get_service_rate(svc)
        style = "danger" if rate > 0 else "danger"
        rate_label = format_dual_rate(rate)
        custom_row.append(
            btn(
                f"{svc_display_name(svc)} — {rate_label}",
                E_ADMIN_CASH,
                callback_data=f"adm_set_rate_c_{idx}",
                style=style,
            )
        )
        if len(custom_row) == 2:
            rows.append(custom_row)
            custom_row = []
    if custom_row:
        rows.append(custom_row)

    rows.append([btn("Profile & Finance USD Rate: ৳" + f"{get_usd_to_bdt_rate():g}", E_ADMIN_CASH, callback_data="adm_usd_exchange", style="danger")])
    rows.append([btn("➕ Add", E_FWD_OK, callback_data="adm_add_earning_service", style="danger")])
    # No separate Stock Number Rates menu: all stock uses the service rate.
    rows.append([btn("🗑 Remove", E_BROADCAST_FAIL, callback_data="adm_remove_earning_service", style="danger")])
    rows.append([btn("Minimum Withdraw", E_ADMIN_CASH, callback_data="adm_set_min_withdraw", style="danger")])
    rows.append([btn("🔗 Referral Commission / Bonus", E_RK_REFER_EARN, callback_data="adm_referral_settings", style="danger")])


    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
    return markup(*rows)

# ================= TELEGRAM CLIENTS =================
_tg_client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=2.5, read=10.0, write=6.0, pool=5.0),
    limits=httpx.Limits(max_connections=200, max_keepalive_connections=60, keepalive_expiry=90),
    http2=False,
)


def extract_otp(msg, svc):
    svc_lower = svc.lower()

    if svc_lower == "whatsapp":
        # Format 1: "274-299" or "274.299" (dash/dot separated 3+3)
        wa_dash = re.search(r"(\d{3})[\.\-](\d{3})", msg)
        if wa_dash:
            return wa_dash.group(1) + wa_dash.group(2)
        # Format 2: "274 299" (space separated 3+3)
        wa_space = re.search(r"(\d{3})\s(\d{3})(?!\d)", msg)
        if wa_space:
            return wa_space.group(1) + wa_space.group(2)
        # Format 3: plain 6-digit "274299"
        wa6 = re.search(r"\b(\d{6})\b", msg)
        if wa6:
            return wa6.group(1)
        return None

    if svc_lower == "facebook":
        # "New FB" (NEWFB_API_SVC) number gulo API te "Facebook" hishebe ashe,
        # kintu asol SMS ta hoy Instagram-style — majhe space diye 3+3
        # (e.g. "651 389"). Age eta check na thakay eishob code detect
        # hoto na (N/A hoye jeto) — tai age eta check kora hocche.
        fb_space = re.search(r"\b(\d{3})\s(\d{3})\b", msg)
        if fb_space: return fb_space.group(1) + fb_space.group(2)
        # Facebook: 8-digit first (forgot password), then 6-digit, then 5-digit (new account)
        fb8 = re.search(r"\b(\d{8})\b", msg)
        if fb8: return fb8.group(1)
        fb6 = re.search(r"\b(\d{6})\b", msg)
        if fb6: return fb6.group(1)
        fb5 = re.search(r"\b(\d{5})\b", msg)
        if fb5: return fb5.group(1)
        return None

    if svc_lower == "newfb" or svc_lower == "instagram":
        # Instagram: space-separated 3+3 format (e.g. "042 915")
        ig_space = re.search(r"\b(\d{3})\s(\d{3})\b", msg)
        if ig_space: return ig_space.group(1) + ig_space.group(2)
        # Instagram: continuous 6-digit format (e.g. "042915")
        ig6 = re.search(r"\b(\d{6})\b", msg)
        if ig6: return ig6.group(1)
        return None

    if svc_lower == "telegram":
        # Telegram nijeder login code onek shomoy PROTITA digit-er majhe
        # dash/space diye pathay (e.g. "Login code: 6-3-9-1-5") — eta
        # Telegram-er nijer anti-phishing format, jate code auto-copy
        # kora na jay. Age eta match na kore shudhu plain "\d{5}"/"\d{6}"
        # check hoto — match na pele None return hoto, r shei None
        # otp_str niye forward card banate giye (bold_button) crash kore
        # SHOLO OTP-i silently hariye jeto (na user, na group — kothao
        # forward hoto na)। Tai age split-digit format check kora hocche.
        tg_split6 = re.search(r"\b(?:\d[\s\-]){5}\d\b", msg)
        if tg_split6: return re.sub(r"[\s\-]", "", tg_split6.group(0))
        tg_split5 = re.search(r"\b(?:\d[\s\-]){4}\d\b", msg)
        if tg_split5: return re.sub(r"[\s\-]", "", tg_split5.group(0))
        tg6 = re.search(r"\b(\d{6})\b", msg)
        if tg6: return tg6.group(1)
        tg5 = re.search(r"\b(\d{5})\b", msg)
        if tg5: return tg5.group(1)
        # Onno kono chena format na mile generic fallback (4-8 digit run) —
        # kokhonoi shorashori None return kore na, jate forward card
        # banate giye kono crash na hoy ar OTP hariye na jay.
        code = re.findall(r"\b\d{4,8}\b", msg)
        return code[0] if code else None

    # Generic fallback — minimum 4 digits, no 3-digit garbage
    code = re.findall(r"\b\d{4,8}\b", msg)
    return code[0] if code else None

# =======================================================
# ✅ Custom Stock Service Name → Main Service Auto-Detection
# =======================================================
# Stock upload er shomoy admin custom/typed service name dile (jemon
# "Facebook1", "New Fb", "Insta Old", "FB Backup" ইত্যাদি) — eituku
# keyword check kore bujhe fele eita আসলে kon MAIN service (Facebook /
# WhatsApp / Telegram / Discord / Instagram) er number, jate sheita
# main service er OFFICIAL emoji shoho forward/display hoy.
#
# Notun keyword lagle shudhu niche list e word ta jog korun.
MAIN_SERVICE_KEYWORDS = {
    "instagram": ["instagram", "insta"],
    "facebook":  ["facebook", "fb"],
    "whatsapp":  ["whatsapp", "wapp", "wa"],
    "telegram":  ["telegram", "tele", "tg"],
    "discord":   ["discord", "dc"]}
# Check order — matlab kon service age check hobe. Beshi specific
# (lomba) keyword-er service age check kore, jate "Instagram" jaigay
# bhul kore "fb"/"tg" er moto choto keyword ese match na kore.
_MAIN_SERVICE_CHECK_ORDER = ["instagram", "facebook", "whatsapp", "telegram", "discord"]

def detect_main_service(custom_name: str):
    """Custom/typed service name (stock upload) theke main (official)
    service key ber korar chesta kore — match na pele None."""
    if not custom_name:
        return None
    name_norm = re.sub(r'[^a-z0-9]', '', custom_name.lower())
    for main_key in _MAIN_SERVICE_CHECK_ORDER:
        for kw in MAIN_SERVICE_KEYWORDS[main_key]:
            if kw in name_norm:
                return main_key
    return None

# Main 5 official service — raw emoji id + unicode fallback (button icons
# need the RAW id, message text needs the ce()-wrapped html, so both are
# kept, built from the same source).
_MAIN_SVC_EMOJI_ID = {
    'facebook':  E_SVC_FACEBOOK,
    'whatsapp':  E_SVC_WHATSAPP,
    'newfb':     E_SVC_INSTAGRAM,
    'instagram': E_SVC_INSTAGRAM,
    'telegram':  E_SVC_TELEGRAM,
    'discord':   E_SVC_DISCORD_SVC}
_MAIN_SVC_FALLBACK = {
    'facebook': "📕", 'whatsapp': "🛡", 'newfb': "📸",
    'instagram': "📸", 'telegram': "📮", 'discord': "🎮"}
_MAIN_SVC_ICON = {k: svc_ce(v, _MAIN_SVC_FALLBACK[k]) for k, v in _MAIN_SVC_EMOJI_ID.items()}
_MAIN_SVC_NAME = {
    'facebook':  'Facebook',
    'whatsapp':  'WhatsApp',
    'newfb':     'Instagram',
    'instagram': 'Instagram',
    'telegram':  'Telegram',
    'discord':   'Discord'}

def _emoji_id_for_key(key: str) -> str:
    """Ekta emoji-key (main service key ba CUSTOM_SERVICE_EMOJIS key) theke
    RAW custom emoji id ber kore — button icon (icon_custom_emoji_id) e
    eita e lage, ce() er HTML wrapper na."""
    if key in _MAIN_SVC_EMOJI_ID:
        return _MAIN_SVC_EMOJI_ID[key]
    if key in CUSTOM_SERVICE_EMOJIS and CUSTOM_SERVICE_EMOJIS[key]:
        return CUSTOM_SERVICE_EMOJIS[key]
    return E_SVC_DEFAULT

def _icon_for_emoji_key(key: str) -> str:
    """Emoji-key theke message-text-e boshanor jonno ce()-wrapped icon."""
    if key in _MAIN_SVC_ICON:
        return _MAIN_SVC_ICON[key]
    if key in CUSTOM_SERVICE_EMOJIS and CUSTOM_SERVICE_EMOJIS[key]:
        return svc_ce(CUSTOM_SERVICE_EMOJIS[key], "🔷")
    return ce(E_SVC_DEFAULT, "🔷")

def _emoji_picker_options():
    """Stock upload er por admin ke dekhano 'select emoji service' list —
    main 5 official service + CUSTOM_SERVICE_EMOJIS e thaka shokol service
    (Netflix, TikTok, Uber ইত্যাদি)। Returns [(key, label), ...]."""
    opts = [(k, _MAIN_SVC_NAME[k]) for k in ["facebook", "whatsapp", "telegram", "discord", "instagram"]]
    opts += [(k, k.title()) for k in CUSTOM_SERVICE_EMOJIS.keys()]
    return opts

def match_emoji_key_by_text(text: str):
    """Admin je service name TYPE kore pathay (jemon 'facebook', 'Netflix') —
    eita theke shothik emoji-key ber kore, so many pages click na kore
    shorashori match hoye jay. Match na pele None.
    Priority: (1) exact main-service key (case-insensitive)
              (2) exact CUSTOM_SERVICE_EMOJIS key (case-insensitive)
    NOTE: fuzzy auto-detection r use kora hoy na — exact match na hole
    admin ke list theke MANUALLY select korte hobe, kono bhul auto-guess
    (e.g. "dc" keyword accidentally match kore Discord dhore neya) hobe na."""
    if not text:
        return None
    key = text.strip().lower()
    if key in _MAIN_SVC_NAME:
        return key
    if key in CUSTOM_SERVICE_EMOJIS:
        return key
    return None

def emoji_picker_keyboard(page: int = 0) -> dict:
    """Paginated inline keyboard — protita button e shei service er nijer
    emoji thake, admin tap korle shei emoji stock-e typed custom service
    name er shathe bind hoye jay. Onek gulo page click na kore shorashori
    naam type korar jonno 'Type Service Name' button-o thake."""
    opts = _emoji_picker_options()
    state.emoji_pick_options = [k for k, _ in opts]
    per_page = 10
    total = len(opts)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    start = page * per_page
    chunk = opts[start:start + per_page]

    rows = []
    for idx, (key, label) in enumerate(chunk, start=start):
        rows.append([btn(label, _emoji_id_for_key(key), callback_data=f"svcemoji_pick_{idx}", style="danger")])

    nav_row = []
    if page > 0:
        nav_row.append(btn("Prev", E_TOOL_BACKBUTTON, callback_data=f"svcemoji_pg_{page-1}", style="danger"))
    nav_row.append(btn(f"📄 {page + 1}/{total_pages}", E_TOOL_REFRESHING, callback_data="noop", style="danger"))
    if page + 1 < total_pages:
        nav_row.append(btn("Next", E_TOOL_REFRESHING, callback_data=f"svcemoji_pg_{page+1}", style="danger"))
    rows.append(nav_row)

    rows.append([btn("✏️ Type Service Name", E_ADMIN_TOOL, callback_data="svcemoji_typemode", style="danger")])
    rows.append([btn("Skip (Default Emoji)", E_SVC_DEFAULT, callback_data="svcemoji_skip", style="danger")])
    rows.append([btn("Close", E_BROADCAST_FAIL, callback_data="close_menu", style="danger")])
    return markup(*rows)

def _resolve_service_icon(svc: str) -> str:
    """Kono service string (real key ba custom/typed name) theke shobcheye
    upojukto custom-emoji-wrapped icon return kore — kokhonoi khali string
    return kore na, tai forward card kokhono emoji chara thake na.
    Priority: (0) admin-picked mapping (service_emoji_map, stock upload
    emoji-picker theke set kora — MANUALLY select kora)  (1) exact
    main-service key  (2) admin-set CUSTOM_SERVICE_EMOJIS exact match
    (3) default 🔷 fallback.
    NOTE: fuzzy/auto keyword-detection (detect_main_service) ICHCHHAKRITO
    bhabe use kora hoy na — eta short keyword (jemon 'dc', 'fb', 'wa')
    onno service name-er majhe accidentally match kore bhul service e
    dhore nito (e.g. "Friend Cookies" -> "...frien-D-C-ookies..." e "dc"
    match kore bhul kore Discord dhore nito). Admin nijei button theke
    manually service select korbe, kono auto-guess hobe na."""
    key = svc.strip().lower()
    mapped_key = state.service_emoji_map.get(key)
    if mapped_key:
        return _icon_for_emoji_key(mapped_key)
    if key in _MAIN_SVC_ICON:
        return _MAIN_SVC_ICON[key]
    if key in CUSTOM_SERVICE_EMOJIS and CUSTOM_SERVICE_EMOJIS[key]:
        return svc_ce(CUSTOM_SERVICE_EMOJIS[key], "🔷")
    return ce(E_SVC_DEFAULT, "🔷")

def svc_tag(svc):
    key = svc.strip().lower()
    if key not in state.service_emoji_map and key in _MAIN_SVC_NAME:
        return f'{_MAIN_SVC_ICON[key]} {_MAIN_SVC_NAME[key]}'
    # Custom (manually typed) service — real label + best-matched icon,
    # e.g. "Facebook1" -> Facebook emoji + "Facebook1" label.
    return f'{_resolve_service_icon(svc)} {svc_display_name(svc)}'

def svc_tag_icon(svc):
    return _resolve_service_icon(svc)

def _resolve_forward_icon(svc: str) -> str:
    """OTP Forward Card (group)-e pathanor jonno ALADA icon resolver.
    _resolve_service_icon() theke ei function-ta ইচ্ছাকৃতভাবে আলাদা —
    eta state.service_emoji_map (admin-picked mapping) ba fuzzy
    detect_main_service() kichhui check kore na, tai kono picker/step
    complete korar dorkar nei. Shudhu FORWARD_CARD_SERVICE_EMOJIS
    dictionary-te ekta emoji id boshiye dile-i shorashori Forward Card
    e shei emoji show hoye jabe.
    Priority: (1) main 5 official service emoji (Facebook/WhatsApp/
    Telegram/Discord/Instagram)  (2) FORWARD_CARD_SERVICE_EMOJIS exact
    (case-insensitive) match  (3) default 🔷 fallback."""
    key = svc.strip().lower()
    if key in _MAIN_SVC_ICON:
        return _MAIN_SVC_ICON[key]
    if key in FORWARD_CARD_SERVICE_EMOJIS and FORWARD_CARD_SERVICE_EMOJIS[key]:
        return svc_ce(FORWARD_CARD_SERVICE_EMOJIS[key], "🔷")
    return ce(E_SVC_DEFAULT, "🔷")

def fwd_svc_icon(svc):
    """OTP Forward Card (OTP_GROUP_ID e jaowa card)-e use korar
    jonno — shudhu ei card e FORWARD_CARD_SERVICE_EMOJIS dictionary
    effect kore, onno kono card e na."""
    return _resolve_forward_icon(svc)

def svc_display_name(svc: str) -> str:
    key = svc.strip().lower()
    if key in _MAIN_SVC_NAME:
        return _MAIN_SVC_NAME[key]
    # Custom/manually typed service name — keep it nicely capitalized
    return svc.strip().title()

def extract_otp_code(text) -> str:
    """Generic OTP/code extractor used by Auto Captcha Panels, where the
    service isn't known ahead of time (unlike extract_otp() above which is
    service-aware). Same logic as the standalone panel-login module."""
    clean_text = re.sub(r'[\u200B-\u200D\uFEFF]', '', str(text))

    # 1. Multi-part OTPs (e.g. 123-456)
    multi_part = re.search(r'(\d{3}[-\s]+\d{3})|(\d{2}[-\s]+\d{2}[-\s]+\d{2})', clean_text)
    if multi_part:
        return multi_part.group(0).replace(" ", "")

    # 2. Keyword-based extraction
    otp_keywords = ['code', 'is', 'otp', 'pin', 'verification', 'auth', 'رمز', 'your code']
    keywords_pattern = '|'.join(otp_keywords)
    keyword_match = re.search(rf'(?:{keywords_pattern})\s*(?:is|:|-|=)?\s*([a-z0-9]{{4,10}})', clean_text, re.I)
    if keyword_match and keyword_match.group(1).isdigit():
        return keyword_match.group(1)

    keyword_match_rev = re.search(rf'([a-z0-9]{{4,10}})\s*(?:is your|is the|code)', clean_text, re.I)
    if keyword_match_rev and keyword_match_rev.group(1).isdigit():
        return keyword_match_rev.group(1)

    # 3. Google OTP
    g_match = re.search(r'G-(\d{6})', clean_text, re.IGNORECASE)
    if g_match:
        return g_match.group(1)

    # 4. Digit sequences fallback
    digit_matches = re.findall(r'(?<!\d)\d{4,8}(?!\d)', clean_text)
    if digit_matches:
        return digit_matches[0]

    return None

# ================= ADMIN FORWARD POOL MONITOR =================
async def _get_forward_pool(pool_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT id, service, interval_seconds, languages, status FROM forward_pools WHERE id = ?",
            (pool_id,)
        ) as cursor:
            return await cursor.fetchone()

async def _get_active_forward_pool_ids() -> list[int]:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT id FROM forward_pools WHERE status = 'active' ORDER BY id") as cursor:
            return [int(row[0]) for row in await cursor.fetchall()]

async def create_forward_pool(service: str, interval_seconds: int, languages: list[str], numbers: list[dict], created_by: int) -> int:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            """INSERT INTO forward_pools
               (service, interval_seconds, languages, status, created_by, created_at, started_at)
               VALUES (?, ?, ?, 'active', ?, ?, ?)""",
            (service, interval_seconds, json.dumps(languages), created_by, created_at, created_at)
        )
        pool_id = cursor.lastrowid
        await db.executemany(
            "INSERT OR IGNORE INTO forward_pool_numbers (pool_id, phone_number, country) VALUES (?, ?, ?)",
            [(pool_id, item["number"], item["country"]) for item in numbers]
        )
        await db.commit()
    return int(pool_id)

async def _get_forward_pool_numbers(pool_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT phone_number, country, sms_seen_count FROM forward_pool_numbers WHERE pool_id = ? ORDER BY phone_number",
            (pool_id,)
        ) as cursor:
            return await cursor.fetchall()

async def _mark_forward_pool_seen(pool_id: int, phone_number: str, seen_count: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE forward_pool_numbers SET sms_seen_count = ? WHERE pool_id = ? AND phone_number = ?",
            (seen_count, pool_id, phone_number)
        )
        await db.commit()

async def stop_forward_pool(pool_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE forward_pools SET status = 'stopped' WHERE id = ?", (pool_id,))
        await db.commit()
    task = state.forward_pool_tasks.pop(pool_id, None)
    if task and not task.done():
        task.cancel()

async def _send_forward_pool_otp(
    pool: tuple,
    number: str,
    country: str,
    message: str,
    otp: str,
    display_languages: list[str] | None = None,
):
    pool_id, service, _interval, languages_json, _status = pool
    target_chat = OTP_GROUP_ID
    if not target_chat:
        print(f"[ForwardPool {pool_id}] No OTP_GROUP_ID configured; skipped send.")
        return
    try:
        languages = json.loads(languages_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        languages = ["en"]
    if display_languages:
        languages = display_languages
    text = forward_pool_text(number, country, service, message, otp, languages)
    # Copy OTP button sits above the Channel and Get Number buttons.
    rows = [[btn(bold_button(str(otp)), E_FWD_OTP, copy_text=str(otp), style="danger")]]
    rows.append([
        btn(bold_button("Channel"), E_FWD_BELL, url=FWD_CHANNEL_URL, style="danger"),
        btn(bold_button("Get Number"), E_RK_GET_NUM, url=FWD_GET_NUMBER_URL, style="__status_red__"),
    ])
    try:
        flag_emoji, short_code = get_country_parts(number)
        card_caption = (
            f"{fwd_svc_icon(service)} | {flag_emoji} <b>{html.escape(str(short_code))}</b> "
            f"<b>{html.escape(hide_number(number))}</b> | {lang_tag(FORWARD_POOL_LANGUAGE_LABELS.get((languages or ['en'])[0], 'English'))}\n\n"
            f"{html.escape(str(message))}"
        )
        await send_dynamic_otp_card(target_chat, number, country, service, otp, card_caption, markup(*rows))
    except Exception as err:
        print(f"[ForwardPool {pool_id}] Forward error: {err}")

async def forward_pool_monitor(pool_id: int):
    """Generate one clearly-labelled test OTP per interval, rotating numbers.

    This pool is intentionally independent from provider OTP feeds. Uploaded
    numbers are identifiers shown in the card; they are not activated at a
    carrier/provider and no real verification message is claimed.
    """
    tag = f"[ForwardPool {pool_id}]"
    cursor_index = 0
    print(f"{tag} simulated OTP generator started.")
    while True:
        try:
            pool = await _get_forward_pool(pool_id)
            if not pool or pool[4] != "active":
                print(f"{tag} generator stopped.")
                return

            interval = max(1, min(int(pool[2] or 15), 3600))
            pool_rows = await _get_forward_pool_numbers(pool_id)
            if pool_rows:
                number, country, _seen_count = pool_rows[cursor_index % len(pool_rows)]
                try:
                    languages = json.loads(pool[3])
                except (TypeError, ValueError, json.JSONDecodeError):
                    languages = ["en"]
                selected_languages = [
                    code for code in languages if code in FORWARD_POOL_LANGUAGE_LABELS
                ] or ["en"]
                language = selected_languages[cursor_index % len(selected_languages)]
                otp = str(secrets.randbelow(900000) + 100000)
                message = generated_forward_pool_message(pool[1], otp, [language])
                await _send_forward_pool_otp(pool, number, country, message, otp, [language])
                await _mark_forward_pool_seen(pool_id, number, (_seen_count or 0) + 1)
                cursor_index += 1
                print(
                    f"{tag} generated one simulated OTP "
                    f"(rotation {cursor_index}, language={language})."
                )

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            print(f"{tag} cancelled.")
            return
        except Exception as err:
            print(f"{tag} generator error: {err}")
            await asyncio.sleep(3)

# ================= OTP MONITOR (Multi-Provider Viewstats API Stock) =================

def _provider_request_url(provider: str) -> str:
    """Return provider URL with any embedded token query parameter removed.

    Some older settings stored the API token directly inside the URL.  The
    monitor also sends the current token as a query parameter, so an old
    embedded token could silently override a newly changed token.  Keep all
    other query parameters intact and let the current setting supply auth.
    """
    raw = (get_provider_url(provider) or "").strip()
    if not raw:
        return raw
    try:
        parts = urlsplit(raw)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if k.lower() not in {"token", "api_token", "apikey", "api_key"}]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return raw


def _first_value(record: dict, keys) -> str:
    """Get the first non-empty value from a provider SMS record.
    Provider panels may change capitalization or use camelCase names.
    """
    if not isinstance(record, dict):
        return ""
    normalized = {}
    for actual_key, value in record.items():
        nk = re.sub(r"[^a-z0-9]", "", str(actual_key).lower())
        normalized.setdefault(nk, value)
    for key in keys:
        nk = re.sub(r"[^a-z0-9]", "", str(key).lower())
        value = normalized.get(nk)
        if value is not None and str(value).strip() != "":
            return str(value)
    return ""


def _extract_provider_sms_rows(payload):
    """Normalize common SMS API response shapes into SMS record dictionaries.

    Handles nested wrappers, case-insensitive wrapper names, a single SMS
    object, and simple number->message mappings.  Never raises on malformed
    provider data.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        # Some APIs return JSON text inside a field; accept it when possible.
        if isinstance(payload, str):
            try:
                decoded = json.loads(payload)
                return _extract_provider_sms_rows(decoded)
            except Exception:
                return []
        return []

    number_keys = {"num", "number", "phone", "mobile", "msisdn", "to", "receiver", "destination", "phone_number", "recipient"}
    message_keys = {"message", "msg", "sms", "body", "content", "text", "sms_text", "message_text"}
    norm_keys = {re.sub(r"[^a-z0-9]", "", str(k).lower()) for k in payload.keys()}
    if (norm_keys & {re.sub(r"[^a-z0-9]", "", k.lower()) for k in number_keys} and
            norm_keys & {re.sub(r"[^a-z0-9]", "", k.lower()) for k in message_keys}):
        return [payload]

    preferred = ("data", "records", "messages", "results", "items", "rows", "sms",
                 "result", "response", "payload", "list")
    preferred_norm = {re.sub(r"[^a-z0-9]", "", k.lower()) for k in preferred}

    # First inspect known wrapper names.
    for key, value in payload.items():
        nk = re.sub(r"[^a-z0-9]", "", str(key).lower())
        if nk not in preferred_norm:
            continue
        if isinstance(value, list):
            rows = [item for item in value if isinstance(item, dict)]
            if rows:
                return rows
        elif isinstance(value, (dict, list, str)):
            rows = _extract_provider_sms_rows(value)
            if rows:
                return rows

    # Finally support simple mappings such as {"8801...": "Your code is 123456"}.
    mapped_rows = []
    for key, value in payload.items():
        raw_key = str(key).strip()
        if clean_number(raw_key) and isinstance(value, str) and value.strip():
            mapped_rows.append({"number": raw_key, "message": value})
    return mapped_rows


def _provider_sms_signature(provider: str, phone: str, service: str, sms: dict) -> str:
    """Stable dedupe key for one provider SMS row.

    Prefer a provider message/id/timestamp when present; otherwise fall back
    to number + service + message. This prevents the same API row from being
    forwarded repeatedly while still allowing multiple different messages.
    """
    provider_id = _first_value(sms, ("id", "sms_id", "smsId", "message_id", "messageId", "record_id", "recordId"))
    timestamp = _first_value(sms, ("timestamp", "time", "date", "datetime", "created_at", "createdAt", "received_at", "receivedAt"))
    _num, msg = _provider_sms_fields(sms)
    raw = "|".join([str(provider or ""), clean_number(phone), str(service or "").strip().lower(),
                     provider_id, timestamp, str(msg or "").strip()])
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


async def _claim_sms_signature(signature: str) -> bool:
    """Atomically claim an SMS signature; True only for the first observer."""
    if not signature:
        return True
    if signature in state.sent_sms_cache:
        return False
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            cur = await db.execute("INSERT OR IGNORE INTO sent_sms (sms_hash) VALUES (?)", (signature,))
            await db.commit()
            claimed = cur.rowcount == 1
        if claimed:
            state.sent_sms_cache.add(signature)
        return claimed
    except Exception as err:
        # If the dedupe table itself is temporarily unavailable, don't drop an
        # otherwise valid OTP. The normal number counter still protects the
        # common case.
        print(f"[SMS Dedupe] Claim warning: {err}")
        return True


def _provider_sms_fields(sms: dict):
    """Return normalized (number, message) values from a provider row."""
    num = _first_value(sms, (
        "num", "number", "phone", "mobile", "msisdn", "to", "receiver",
        "destination", "phone_number", "phoneNumber", "recipient"
    ))
    msg = _first_value(sms, (
        "message", "msg", "sms", "body", "content", "text", "sms_text",
        "smsText", "message_text", "messageText"
    ))
    return num, msg


def _provider_response_is_usable(payload) -> bool:
    """Accept success/ok responses while still rejecting explicit API errors."""
    if not isinstance(payload, dict):
        return isinstance(payload, list)
    status = payload.get("status", payload.get("success", payload.get("ok")))
    if status is None:
        return bool(_extract_provider_sms_rows(payload))
    if isinstance(status, str):
        return status.strip().lower() in {"success", "ok", "true", "1", "200"}
    return bool(status)


async def _release_sms_signature(signature: str):
    """Release a claimed SMS when Telegram delivery failed, so the next poll can retry."""
    if not signature:
        return
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("DELETE FROM sent_sms WHERE sms_hash = ?", (signature,))
            await db.commit()
    except Exception as err:
        print(f"[SMS Dedupe] Release warning: {err}")
    state.sent_sms_cache.discard(signature)


async def sms_provider_monitor(provider: str):
    """Generic monitor loop — one instance runs per configured panel.
    All panels share the same 'numbers' stock table; the first provider
    returning a new SMS claims it for that number."""
    tag = f"[{PROVIDER_LABELS.get(provider, provider)}Monitor]"
    print(f"{tag} Background SMS Checker loop started.")
    # HTTP 429 protection: exponentially back off when a provider rate-limits us.
    rate_limit_delay = 0
    rate_limit_hits = 0

    while True:
        if not provider_configured(provider):
            # Not configured yet — wait and recheck, so setting it later (no restart) activates the loop
            await asyncio.sleep(1)
            continue
        try:
            # 1. Release expired busy stock numbers (> 30 mins).
            # Jei number gulo ei shomoy er moddhe already OTP peye geche
            # (otp_received = 1), segulo 'available' e ferot na diye
            # 'retired' e pathiye dei — permanently retired thake, kono
            # user ke r deya hoy na. Jegulo kono OTP-i payni, segulo age
            # jemon chilo temon-i 'available' e ferot jay.
            current_time = int(time.time())
            async with aiosqlite.connect(DB_FILE) as db:
                cursor = await db.execute(
                    """UPDATE numbers
                       SET status = 'retired',
                           assigned_to = NULL,
                           assign_time = NULL
                       WHERE status = 'busy' AND (? - assign_time) > 1800""",
                    (current_time,)
                )
                released_count = cursor.rowcount
                await db.commit()
            if released_count > 0:
                print(f"{tag} Automatically released {released_count} expired Stock Numbers.")
            
            # 2. Get active leased busy Stock Numbers (shathe koyta SMS already
            # forward kora hoyeche shei count-o niye ashi, notun SMS chena jonno)
            async with aiosqlite.connect(DB_FILE) as db:
                async with db.execute(
                    "SELECT phone_number, service, country, assigned_to, sms_seen_count, otp_rate FROM numbers WHERE status = 'busy'"
                ) as cursor:
                    rows = await cursor.fetchall()
            
            if not rows:
                await asyncio.sleep(get_provider_interval(provider))
                continue
            
            # Normalize DB numbers too, so +country-code / spaces / punctuation
            # from stock upload cannot prevent an API number from matching.
            # Keep the original DB key for the final UPDATE statement.
            watched_dict = {}
            for row in rows:
                db_phone = row[0]
                clean_db_phone = clean_number(db_phone)
                if clean_db_phone:
                    watched_dict[clean_db_phone] = (db_phone, row[1], row[2], row[3], row[4] or 0, row[5])
            
            # 3. Call this provider API.  Always use the CURRENT token as a
            # parameter; _provider_request_url removes stale embedded tokens.
            token = get_provider_token(provider)
            params = {"token": token, "records": 100}
            client = await get_provider_http_client(provider)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = await client.get(_provider_request_url(provider), params=params, headers=headers)
            
            if response.status_code == 429:
                # Respect Retry-After when the provider supplies it; otherwise
                # use a safe exponential backoff. This prevents a 1-second
                # polling loop from continuously hitting the API rate limit.
                rate_limit_hits += 1
                retry_after = response.headers.get("Retry-After", "")
                try:
                    retry_seconds = max(5, int(float(retry_after))) if retry_after else 0
                except (TypeError, ValueError):
                    retry_seconds = 0
                rate_limit_delay = min(60, max(5, retry_seconds or (5 * (2 ** min(rate_limit_hits - 1, 3)))))
                print(f"{tag} Viewstats rate limited (429). Backing off for {rate_limit_delay}s.")
                await asyncio.sleep(rate_limit_delay)
                continue

            if response.status_code == 200:
                rate_limit_hits = 0
                rate_limit_delay = 0
                sms_list = []
                try:
                    data = response.json()
                except Exception as json_err:
                    print(f"{tag} API returned non-JSON response: {json_err}")
                    data = None

                if _provider_response_is_usable(data):
                    sms_list = _extract_provider_sms_rows(data)
                    if not sms_list:
                        preview = str(data)[:800]
                        print(f"{tag} API OK but no SMS rows found. Response preview={preview}")
                        await asyncio.sleep(get_provider_interval(provider))
                        continue
                else:
                    if isinstance(data, dict):
                        print(f"{tag} API-level error/unusable response: {str(data)[:500]}")
                    else:
                        print(f"{tag} API returned an unusable payload: {type(data).__name__}")
                    await asyncio.sleep(get_provider_interval(provider))
                    continue

                # Proti number er SMS gulo oldest-first order e group kori.
                # Notun SMS chena hoy purely COUNT die (same behavior as before).
                grouped: dict = {}
                for sms in reversed(sms_list):
                    raw_num, _raw_msg = _provider_sms_fields(sms)
                    clean_num = clean_number(raw_num)
                    if clean_num in watched_dict:
                        grouped.setdefault(clean_num, []).append(sms)

                for clean_num, entries in grouped.items():
                    db_phone, service, country, assigned_user, seen_count, number_rate = watched_dict[clean_num]
                    # The provider may return only the latest N rows, so a
                    # count-only check can permanently miss a new OTP when
                    # len(entries) <= seen_count. Use stable row signatures as
                    # the primary dedupe mechanism and keep the count as a
                    # fallback for older rows without useful content.
                    candidates = []
                    for sms in entries:
                        raw_num, msg = _provider_sms_fields(sms)
                        num = raw_num or clean_num
                        if not msg:
                            continue
                        signature = _provider_sms_signature(provider, num, service, sms)
                        if signature in state.sent_sms_cache:
                            continue
                        candidates.append((sms, num, msg, signature))

                    if not candidates:
                        continue

                    for sms, num, msg, signature in candidates:
                        # Claim before sending so two provider loops cannot
                        # forward the same API row at the same time.
                        if not await _claim_sms_signature(signature):
                            continue
                            
                        # OTP successfully received. Status 'busy'-i thake (DELETE
                        # kora hoy na), tai loop eta watch korte thakbe — porer SMS
                        # gulo-o forward hobe. 30 min busy-timeout expire hole
                        # otp_received=1 thakle number-ta 'retired' e chole jabe.
                        otp_str = extract_otp(msg, service)
                        # Kono format e-i match na hole (khub rare) — bold_button()
                        # None niye crash kore r shole shole OTP-i hariye jeto,
                        # tai defensive fallback rakha holo.
                        if not otp_str:
                            otp_str = "N/A"
                        
                        # Calculate the earning first, but DO NOT credit it until
                        # Telegram successfully accepts the user's OTP message.
                        # This prevents duplicate balance/referral credits when a
                        # transient Telegram error causes an OTP retry.
                        base_rate = get_effective_earning_rate(service, country, stock_only=True)
                        earned = (float(number_rate) if number_rate is not None else base_rate)
                        if earned < 0:
                            earned = 0.0

                        # Keep the original country parts for the group forward card.
                        flag_emoji, short_code = get_country_parts(num)
                        country_label = country_name_for_number(num)
                        country_flag = country_flag_emoji(country_label)
                        if country_flag == "🏳️":
                            country_flag, _country_short = get_country_parts(num)
                        svc_icon = svc_tag_icon(service) or svc_display_name(service)
                        current_balance = await get_balance(assigned_user)
                        projected_balance = current_balance + earned
                        otp_text = (
                            f"🏳 <code>{html.escape(str(num))}</code> 💰<b>{earned:.2f}Tk</b>\n"
                            f"{svc_icon} {html.escape(svc_display_name(service))} · {country_flag} "
                            f"{html.escape(country_label)}\n"
                            f"🏦 Balance: <b>{projected_balance:.2f}Tk</b>"
                        )
                        otp_markup = markup(
                            [btn(otp_str, E_OTP_KEY, copy_text=otp_str, style="danger")],
                        )

                        # Broadcast forward notification to channel/group.
                        fwd_icon = fwd_svc_icon(service)
                        f_text = (
                            f"☎️ <b>{html.escape(forward_mask_number(num))}</b>\n"
                            f"{fwd_icon} <b>{html.escape(svc_display_name(service))}</b> · "
                            f"{country_flag} <b>{html.escape(country_label)}</b>"
                        )
                        f_markup = markup(
                            [btn(bold_button(otp_str), E_FWD_OTP, copy_text=otp_str, style="danger")],
                            [btn(bold_button("Channel"), E_FWD_BELL, url=FWD_CHANNEL_URL, style="danger"),
                             btn(bold_button("Get Number"), E_RK_GET_NUM, url=FWD_GET_NUMBER_URL, style="__status_red__")]
                        )

                        try:
                            if not assigned_user:
                                print(f"{tag} User send skipped: no assigned user for {num}")
                                await _release_sms_signature(signature)
                                continue
                            await asyncio.wait_for(
                                bot.send_message(chat_id=assigned_user, text=otp_text, reply_markup=otp_markup),
                                timeout=8.0
                            )
                        except Exception as err:
                            print(f"{tag} User send error: {err}")
                            await _release_sms_signature(signature)
                            continue

                        # Telegram accepted the user message. Now perform the
                        # financial/history writes exactly once for this SMS.
                        check_daily_reset()
                        state.daily_otp_count += 1
                        await record_panel_otp(provider)
                        if earned:
                            await add_balance(assigned_user, earned)
                            await credit_referral_commission(assigned_user, earned)
                        if assigned_user not in state.otp_history:
                            state.otp_history[assigned_user] = []
                        state.otp_history[assigned_user].append({
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "ts":        time.time(),
                            "phone":     num,
                            "service":   service.upper(),
                            "otp":       otp_str
                        })
                        await record_user_otp(assigned_user, service, earned)

                        # Only after user delivery succeeds do we attempt the
                        # group/card notification. A group failure never causes
                        # the user's OTP or balance to be lost.
                        try:
                            if not OTP_GROUP_ID:
                                print(f"{tag} Forward skipped: OTP_GROUP_ID is not configured")
                            else:
                                await send_dynamic_otp_card(
                                    OTP_GROUP_ID, num, country_label, service, otp_str, f_text, f_markup
                                )
                        except Exception as err:
                            print(f"{tag} Forward error: {err}")

                    # Ei number er shob notun SMS forward howar por total
                    # count DB te update kore dei, r otp_received=1 mark kori
                    # (permanent retire logic er jonno)
                    async with aiosqlite.connect(DB_FILE) as db:
                        await db.execute(
                            "UPDATE numbers SET sms_seen_count = ?, otp_received = 1 WHERE phone_number = ? AND service = ? AND assigned_to = ?",
                            (max(seen_count, len(entries)), db_phone, service, assigned_user)
                        )
                        await db.commit()

                    # Refresh only after the write connection is fully closed.
                    # This guarantees the card reads the committed otp_received=1
                    # value and turns only this user's number RED.
                    await _refresh_number_card_after_otp(
                        assigned_user, service, country
                    )
            else:
                try:
                    body_preview = response.text[:500]
                except Exception:
                    body_preview = "<unreadable response body>"
                print(f"{tag} Viewstats status error code: {response.status_code}; body={body_preview}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"{tag} Loop error: {e}")
        
        await asyncio.sleep(get_provider_interval(provider))

# ================= AUTO CAPTCHA PANEL SYSTEM =================
# Logs into a panel's web dashboard (username + password + a simple math
# captcha), keeps the session alive, and periodically scrapes the SMS/CDR
# table for OTPs — for panels that don't offer a token+URL viewstats API.

async def attempt_captcha_login(key: str) -> bool:
    """Solves the panel's math captcha, submits the login form, and stores
    a logged-in httpx.AsyncClient in captcha_sessions[key] on success."""
    p = CAPTCHA_PANELS.get(key)
    if not p:
        return False

    # Green Panel (Green SMS) এর সিস্টেম আলাদা — Django login + JSON API,
    # তাই ওটার জন্য পুরোপুরি আলাদা login function ব্যবহার হয়।
    if (p.get("panel_type") or "generic") == "greennews":
        return await attempt_greennews_login(key)

    login_url = (p.get("login_url") or "").strip()
    if not login_url.startswith("http"):
        login_url = "http://" + login_url
    if not login_url.lower().endswith('/login') and not login_url.lower().endswith('.php'):
        login_url = f"{login_url.rstrip('/')}/login"

    client = httpx.AsyncClient(
        timeout=15,
        follow_redirects=True,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
    )

    try:
        res = await client.get(login_url)
        soup = BeautifulSoup(res.text, 'html.parser')
        all_text = res.text

        # 1. Solve captcha (simple math: "a + b = ?")
        captcha_match = re.search(r'(\d+\s*[\+\-\*]\s*\d+)\s*[=\?:]', all_text)
        if not captcha_match:
            captcha_match = re.search(r'what is\s*(\d+\s*[\+\-\*]\s*\d+)', all_text, re.I)
        if not captcha_match:
            for el in soup.find_all(["label", "div", "span", "p", "strong"]):
                txt = el.get_text(separator=" ", strip=True)
                if any(op in txt for op in ["+", "-", "*"]):
                    m = re.search(r'(\d+\s*[\+\-\*]\s*\d+)', txt)
                    if m:
                        captcha_match = m
                        break

        captcha_text = captcha_match.group(1) if captcha_match else "0 + 0"
        answer = "0"
        m2 = re.search(r'(\d+)\s*([\+\-\*])\s*(\d+)', captcha_text)
        if m2:
            a, op, b = int(m2.group(1)), m2.group(2), int(m2.group(3))
            answer = str(a + b if op == '+' else a - b if op == '-' else a * b)

        # 2. Find the login form
        form = soup.find("form")
        if not form:
            await _update_captcha_login_status(key, "❌ No login form found")
            return False

        action = form.get("action")
        post_url = urljoin(login_url, action) if action else login_url

        form_data = {}
        for hidden in form.find_all("input", type="hidden"):
            name = hidden.get("name")
            if name:
                form_data[name] = hidden.get("value") or ""

        def _fmatch(keywords):
            def _check(val):
                if not val:
                    return False
                v = val.lower()
                return any(k in v for k in keywords)
            return _check

        user_input = (
            form.find("input", {"name": _fmatch(["user", "email", "id"])}) or
            form.find("input", {"type": "text", "placeholder": _fmatch(["user", "email"])}) or
            form.find("input", {"type": "text"})
        )
        pass_input = (
            form.find("input", {"name": _fmatch(["pass", "password", "passwd"])}) or
            form.find("input", {"type": "password"})
        )
        captcha_input = (
            form.find("input", {"placeholder": _fmatch(["answer", "ans", "code", "verification", "value", "captcha"])}) or
            form.find("input", {"name": _fmatch(["ans", "captcha", "ver", "code"])})
        )

        user_field    = user_input.get("name") if user_input else "username"
        pass_field    = pass_input.get("name") if pass_input else "password"
        captcha_field = captcha_input.get("name") if captcha_input else "answer"

        form_data[user_field] = p.get("username", "")
        form_data[pass_field] = p.get("password", "")
        if captcha_input and captcha_field:
            form_data[captcha_field] = answer

        # 3. Submit
        login_req = await client.post(post_url, data=form_data)

        # 4. Verify login succeeded
        msg_link = (p.get("msg_link") or "").strip()
        if msg_link and not msg_link.startswith("http"):
            msg_link = "http://" + msg_link
        check_url = msg_link if msg_link else f"{login_url.split('/login')[0]}/client/SMSCDRStats"

        check_res = await client.get(check_url)

        login_success_keywords = [
            'logout', 'log out', 'signout', 'sign out',
            'sms reports', 'dashboard', 'cdrs',
            'welcome', 'profile', 'panel', 'inbox',
            'number', 'report', 'home', 'account',
            'client', 'smscdr', 'numberpanel'
        ]
        combined_text = (login_req.text + check_res.text).lower()
        if any(kw in combined_text for kw in login_success_keywords):
            old_client = captcha_sessions.get(key)
            captcha_sessions[key] = client
            if old_client and old_client is not client:
                try: await old_client.aclose()
                except Exception: pass
            await _update_captcha_login_status(key, "✅ Active & Fetching")
            return True
        else:
            uf = user_input.get("name") if user_input else "NOT FOUND"
            pf = pass_input.get("name") if pass_input else "NOT FOUND"
            cf = captcha_input.get("name") if captcha_input else "none"
            await _update_captcha_login_status(key, f"❌ Login Failed (fields: user={uf}, pass={pf}, captcha={cf})")

    except Exception as e:
        await _update_captcha_login_status(key, f"❌ Error: {str(e)[:50]}")
    finally:
        if captcha_sessions.get(key) is not client:
            try: await client.aclose()
            except Exception: pass

    return False

async def fetch_captcha_panel_data(key: str):
    """Uses the logged-in session to pull the panel's SMS/CDR table —
    tries the DataTables AJAX endpoint first, falls back to the raw HTML
    table. Returns (results, raw_html_text). Raises on session expiry."""
    p = CAPTCHA_PANELS[key]

    # Green Panel (Green SMS) — আলাদা Django JSON API fetch logic।
    if (p.get("panel_type") or "generic") == "greennews":
        return await fetch_greennews_data(key)

    client = captcha_sessions[key]
    login_url = (p.get("login_url") or "").strip()
    if not login_url.startswith("http"):
        login_url = "http://" + login_url

    msg_link = (p.get("msg_link") or "").strip()
    if msg_link and not msg_link.startswith("http"):
        msg_link = "http://" + msg_link
    check_url = msg_link if msg_link else f"{login_url.split('/login')[0]}/client/SMSCDRStats"

    res = await client.get(check_url)
    html_text = res.text

    if "login" in html_text.lower() or "signin" in html_text.lower() or any(
        x in html_text for x in ["Sign in to your account", "Please sign in", "Welcome back!"]
    ):
        raise Exception("Session expired")

    soup = BeautifulSoup(html_text, 'html.parser')

    detected_col_count = 7
    # Lamix SMS/CDR tables can have different column positions.  Prefer the
    # actual table headers (e.g. Date | Range | Number | CLI | SMS) over the
    # saved default indexes so Number/SMS are parsed from the correct columns.
    detected_num_idx = None
    detected_msg_idx = None
    for table in soup.find_all('table'):
        header_rows = table.find_all('tr')
        if header_rows:
            first_row_cols = header_rows[0].find_all(['th', 'td'])
            if len(first_row_cols) > detected_col_count:
                detected_col_count = len(first_row_cols)
            for i, cell in enumerate(first_row_cols):
                c_text = re.sub(r'\s+', ' ', cell.get_text(' ', strip=True).lower())
                if re.search(r'\b(number|phone|msisdn|mobile)\b', c_text):
                    detected_num_idx = i
                if re.search(r'\b(sms|message|content|body|text)\b', c_text):
                    detected_msg_idx = i
            if detected_num_idx is not None and detected_msg_idx is not None:
                break

    s_ajax_source = ""
    for script in soup.find_all("script"):
        script_text = script.string or ""
        match = re.search(r'sAjaxSource"?\s*:\s*"([^"]+)"', script_text)
        if match:
            s_ajax_source = match.group(1); break
        match = re.search(r'["\']ajax["\']\s*:\s*["\']([^"\']+)["\']', script_text)
        if match:
            s_ajax_source = match.group(1); break
        match = re.search(r'["\']?ajax["\']?\s*:\s*\{[^}]*["\']?url["\']?\s*:\s*["\']([^"\']+)["\']', script_text)
        if match:
            s_ajax_source = match.group(1); break
        if 'DataTable' in script_text or 'dataTable' in script_text:
            match = re.search(r'"url"\s*:\s*"([^"]+)"', script_text)
            if match:
                s_ajax_source = match.group(1); break

    results = []
    n_col_name = (p.get("num_col_name") or "number").lower()
    m_col_name = (p.get("msg_col_name") or "message").lower()
    n_idx = int(p.get("num_col_idx") or 1) - 1
    m_idx = int(p.get("msg_col_idx") or 2) - 1
    if detected_num_idx is not None:
        n_idx = detected_num_idx
    if detected_msg_idx is not None:
        m_idx = detected_msg_idx

    if s_ajax_source:
        base_url = login_url.split("/client")[0].split("/login")[0].strip()
        if not base_url.startswith("http"):
            base_url = "http://" + base_url

        if s_ajax_source.startswith("http"):
            full_ajax_url = s_ajax_source
        elif s_ajax_source.startswith("/"):
            full_ajax_url = f"{base_url}{s_ajax_source}"
        else:
            last_slash_idx = check_url.rfind("/")
            current_dir = check_url[:last_slash_idx] if last_slash_idx > 0 else check_url.rstrip("/")
            full_ajax_url = f"{current_dir}/{s_ajax_source}"

        if "iDisplayLength" not in full_ajax_url:
            col_search = "&".join([f"sSearch_{i}=&bRegex_{i}=false&bSearchable_{i}=true&bSortable_{i}=true" for i in range(detected_col_count)])
            query_params = f"sEcho=1&iColumns={detected_col_count}&iDisplayStart=0&iDisplayLength=9999&sSearch=&bRegex=false&iSortingCols=1&iSortCol_0=0&sSortDir_0=desc&{col_search}"
            divider = "&" if "?" in full_ajax_url else "?"
            full_ajax_url += f"{divider}{query_params}"

        ajax_headers = {"Referer": check_url, "X-Requested-With": "XMLHttpRequest"}
        ajax_res = await client.get(full_ajax_url, headers=ajax_headers)

        rate_limit_phrases = ["too many times", "try again", "rate limit", "slow down", "429", "blocked"]
        if not ajax_res.text.strip():
            raise Exception("AJAX URL returned empty response. Check your Msg Link setting.")
        if any(ph in ajax_res.text.lower() for ph in rate_limit_phrases) and ajax_res.text.strip()[0] != '{':
            await asyncio.sleep(6)
            ajax_res = await client.get(full_ajax_url, headers=ajax_headers)
        try:
            data_dict = ajax_res.json()
        except Exception:
            raise Exception(f"AJAX response is not valid JSON. Got: {ajax_res.text[:120]!r}")

        for row_val in data_dict.get("aaData", []):
            if not isinstance(row_val, list) or len(row_val) < max(n_idx, m_idx) + 1:
                continue
            num_val = row_val[n_idx] if (0 <= n_idx < len(row_val)) else row_val[2]
            msg_val = row_val[m_idx] if (0 <= m_idx < len(row_val)) else row_val[4]
            clean_num = re.sub(r'\D', '', str(num_val))
            if clean_num and 5 <= len(clean_num) <= 18:
                otp = extract_otp_code(msg_val)
                if otp and len(str(msg_val)) > 4:
                    results.append({"number": clean_num, "message": msg_val, "otp": otp})
    else:
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if not rows:
                continue
            final_n_idx, final_m_idx = n_idx, m_idx
            header_cells = rows[0].find_all(['th', 'td'])
            for i, cell in enumerate(header_cells):
                c_text = cell.get_text(strip=True).lower()
                if n_col_name in c_text: final_n_idx = i
                if m_col_name in c_text: final_m_idx = i
            for row in rows:
                cols = row.find_all(['td', 'th'])
                if all(c.name == 'th' for c in cols):
                    continue
                if len(cols) > max(final_n_idx, final_m_idx):
                    num_text = cols[final_n_idx].get_text(separator=" ", strip=True)
                    msg_text = cols[final_m_idx].get_text(separator=" ", strip=True)
                    clean_num = re.sub(r'\D', '', num_text)
                    if clean_num and 5 <= len(clean_num) <= 18:
                        otp = extract_otp_code(msg_text)
                        if otp and len(msg_text) > 4:
                            results.append({"number": clean_num, "message": msg_text, "otp": otp})

    return results, html_text

# ================= GREEN PANEL (Green SMS) SYSTEM =================
# Green SMS ("Green News") একটা আলাদা ধরনের auto captcha panel — Django
# dashboard, csrfmiddlewaretoken + math captcha দিয়ে login করে, তারপর
# একটা JSON API (/api/messaging/incoming-user/records/) থেকে incoming SMS
# পড়ে (পুরনো টা লোড না হলে HTML table fallback করে)। উপরের generic
# Auto Captcha Panel (form auto-detect + DataTables) থেকে সম্পূর্ণ আলাদা
# system বলে, এটাকে সম্পূর্ণ নিজস্ব login/fetch function হিসেবে রাখা হলো।
# Panel মেনুতেও এটা "🟢 Green Panel" নামে আলাদা section — ইউজারনেম আর
# পাসওয়ার্ড (আর একটা Login URL) দিলেই চালু হয়ে যায়।

_GP_LOGIN_PATH    = "/accounts/login/"
_GP_INCOMING_PATH = "/messaging/incoming/"
_GP_RECORDS_PATH  = "/api/messaging/incoming-user/records/"

_GP_PHONE_HEADERS = (
    "number", "phone", "msisdn", "mobile", "recipient",
    "destination", "receiver", "to", "line", "sim",
)
_GP_MESSAGE_HEADERS = (
    "message", "sms", "content", "body", "text", "otp", "messagebody",
)
_GP_SENDER_HEADERS = ("sender", "from", "cli", "service", "source")
_GP_DATE_HEADERS   = ("date", "time", "received", "created", "timestamp")
_GP_ID_HEADERS     = ("id", "messageid", "message_id", "rowid")

_GP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_GP_BASE_HEADERS = {
    "User-Agent":      _GP_UA,
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"}
_GP_AJAX_HEADERS = {
    "User-Agent":       _GP_UA,
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Accept-Language":  "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest"}


def _gp_base_url(url: str = "") -> str:
    """Login URL বা Base URL বা incoming page — যেকোনোটা দিলেই আসল base বের করে।"""
    value = (url or "").strip().rstrip("/")
    if value and not value.startswith("http"):
        value = "http://" + value
    for suffix in (_GP_LOGIN_PATH.rstrip("/"), "/accounts/login", _GP_INCOMING_PATH.rstrip("/"), "/messaging/incoming"):
        if value.lower().endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
    return value


def _gp_solve_captcha(html_text: str) -> str:
    """Green Panel এর সাধারণ math captcha ("2 + 3 = ?") solve করে।"""
    for pat in [
        r'What is\s*(\d+)\s*\+\s*(\d+)',
        r'(\d+)\s*\+\s*(\d+)\s*=',
        r'captcha[^>]*>\s*(\d+)\s*\+\s*(\d+)',
        r'>(\d+)\s*\+\s*(\d+)<',
        r'(\d+)\s*\+\s*(\d+)',
    ]:
        m = re.search(pat, html_text, re.I)
        if m:
            return str(int(m.group(1)) + int(m.group(2)))
    for pat in [r'What is\s*(\d+)\s*-\s*(\d+)', r'(\d+)\s*-\s*(\d+)\s*=']:
        m = re.search(pat, html_text, re.I)
        if m:
            return str(max(0, int(m.group(1)) - int(m.group(2))))
    try:
        plain = BeautifulSoup(html_text, "html.parser").get_text()
        for pat in [r'(\d+)\s*\+\s*(\d+)', r'(\d+)\s*-\s*(\d+)']:
            m = re.search(pat, plain)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                return str(a + b if "+" in pat else max(0, a - b))
    except Exception:
        pass
    return "0"


def _gp_csrf_token(page_text: str, client: "httpx.AsyncClient") -> str:
    """HTML hidden input / meta tag / csrftoken cookie — যেখান থেকেই পাওয়া যায়, Django CSRF token বের করে।"""
    try:
        soup = BeautifulSoup(page_text or "", "html.parser")
        for selector in (
            'input[name="csrfmiddlewaretoken"]',
            'meta[name="csrf-token"]',
            'meta[name="csrf_token"]',
            '[data-csrf-token]',
            '[data-csrf]',
        ):
            node = soup.select_one(selector)
            if not node:
                continue
            value = node.get("value") or node.get("content") or node.get("data-csrf-token") or node.get("data-csrf")
            value = html.unescape(str(value or "")).strip()
            if value:
                return value
    except Exception:
        pass
    m = re.search(r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']', page_text or "", re.I)
    if not m:
        m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrfmiddlewaretoken["\']', page_text or "", re.I)
    if m:
        return html.unescape(m.group(1)).strip()
    return client.cookies.get("csrftoken", "") or ""


def _gp_form_action(page_text: str, login_url: str) -> str:
    try:
        form = BeautifulSoup(page_text or "", "html.parser").select_one("form")
        action = str(form.get("action") or "").strip() if form else ""
        return urljoin(login_url, action) if action else login_url
    except Exception:
        return login_url


def _gp_header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _gp_find_value(values: dict, aliases: tuple) -> str:
    for key, value in values.items():
        if any(key == alias or (len(alias) >= 3 and alias in key) for alias in aliases):
            if value:
                return str(value)
    return ""


def _gp_normalize_number(value) -> str:
    digits = re.sub(r"[^\d]", "", html.unescape(str(value or "")).strip())
    return digits if 7 <= len(digits) <= 16 else ""


def _gp_is_date_like(value: str) -> bool:
    value = (value or "").strip()
    return bool(re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", value) or re.search(r"\d{1,2}:\d{2}", value))


def _gp_json_rows_to_dicts(text: str) -> list:
    """Green Panel এর JSON records API response থেকে row-dict গুলো বের করে।"""
    text = (text or "").strip()
    if not text or text.startswith("<"):
        return []
    try:
        js = json.loads(text)
    except Exception:
        return []
    if isinstance(js, dict):
        rows = js.get("results") or js.get("data") or js.get("records") or js.get("aaData") or []
    elif isinstance(js, list):
        rows = js
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _gp_dict_row_to_result(row: dict) -> dict | None:
    """একটা JSON row-dict কে {"number","message","otp"} আকারে normalize করে।"""
    lowered = {_gp_header_key(k): v for k, v in row.items()}
    number = _gp_normalize_number(_gp_find_value(lowered, _GP_PHONE_HEADERS))
    message = _gp_find_value(lowered, _GP_MESSAGE_HEADERS)
    if not number or not message:
        return None
    message = html.unescape(str(message))
    otp = extract_otp_code(message)
    if not otp or len(message) <= 4:
        return None
    return {"number": number, "message": message, "otp": otp}


def _gp_cell_text(cell) -> str:
    visible = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
    if visible:
        return visible
    for attr in ("data-value", "data-number", "data-phone", "data-msisdn", "data-message", "data-text", "title"):
        value = re.sub(r"\s+", " ", str(cell.get(attr, "") or "")).strip()
        if value:
            return value
    return ""


def _gp_row_to_result(cells: list, headers: list) -> dict | None:
    """HTML table এর একটা row কে {"number","message","otp"} আকারে normalize করে (JSON API fallback)।"""
    if not cells:
        return None
    values = {}
    if headers and len(headers) == len(cells):
        values = {_gp_header_key(h): c for h, c in zip(headers, cells) if h}

    number = _gp_normalize_number(_gp_find_value(values, _GP_PHONE_HEADERS))
    if not number:
        number = next((_gp_normalize_number(c) for c in cells if not _gp_is_date_like(c)), "")
    if not number:
        return None

    message = _gp_find_value(values, _GP_MESSAGE_HEADERS)
    if not message:
        candidates = [c for c in cells if c and c != number and not _gp_normalize_number(c) and not _gp_is_date_like(c)]
        message = max(candidates, key=len, default="")
    if not message:
        return None

    message = html.unescape(str(message))
    otp = extract_otp_code(message)
    if not otp or len(message) <= 4:
        return None
    return {"number": number, "message": message, "otp": otp}


def _gp_parse_html_table(text: str) -> list:
    """Green Panel এর incoming page (server-rendered HTML table) থেকে SMS row গুলো বের করে।"""
    soup = BeautifulSoup(text or "", "html.parser")
    results = []
    for table in soup.find_all("table"):
        headers = []
        thead = table.find("thead")
        header_rows = (thead.find_all("tr") if thead else []) + table.find_all("tr")
        for header_row in header_rows:
            header_cells = header_row.find_all(["th", "td"], recursive=False)
            if not header_cells:
                continue
            candidate = [_gp_cell_text(c) for c in header_cells]
            normalized = {_gp_header_key(v) for v in candidate if v}
            has_known = any(
                key == alias or (len(alias) >= 3 and alias in key)
                for alias_group in (_GP_PHONE_HEADERS, _GP_MESSAGE_HEADERS, _GP_SENDER_HEADERS, _GP_DATE_HEADERS)
                for alias in alias_group
                for key in normalized
            )
            if any(c.name == "th" for c in header_cells) or has_known:
                headers = candidate
                break

        for row in table.find_all("tr"):
            row_cells = row.find_all(["td", "th"], recursive=False)
            cells = [_gp_cell_text(c) for c in row_cells]
            if not cells:
                continue
            normalized = _gp_row_to_result(cells, headers)
            if normalized:
                results.append(normalized)
    return results


async def attempt_greennews_login(key: str) -> bool:
    """Green Panel এ login করে (CSRF token + math captcha + username/password)
    এবং সফল হলে captcha_sessions[key] এ {"client","base_url"} সেভ করে।"""
    p = CAPTCHA_PANELS.get(key)
    if not p:
        return False

    base_url = _gp_base_url(p.get("login_url") or "")
    if not base_url:
        await _update_captcha_login_status(key, "❌ Invalid Login URL")
        return False
    login_url = f"{base_url}{_GP_LOGIN_PATH}"

    client = httpx.AsyncClient(timeout=25, follow_redirects=True, headers=_GP_BASE_HEADERS)
    try:
        r = await client.get(login_url)
        if r.status_code == 429:
            await _update_captcha_login_status(key, "❌ Rate limited (HTTP 429) — একটু পরে আবার চেষ্টা করুন")
            return False
        if r.status_code >= 400:
            await _update_captcha_login_status(key, f"❌ Login page error (HTTP {r.status_code})")
            return False

        csrf = _gp_csrf_token(r.text, client)
        if not csrf:
            await _update_captcha_login_status(key, "❌ CSRF token পাওয়া যায়নি")
            return False

        captcha_answer = _gp_solve_captcha(r.text)
        post_url = _gp_form_action(r.text, login_url)
        post_data = {
            "csrfmiddlewaretoken": csrf,
            "username": p.get("username", ""),
            "password": p.get("password", ""),
            "captcha_answer": captcha_answer}
        r2 = await client.post(
            post_url,
            data=post_data,
            headers={
                **_GP_BASE_HEADERS,
                "Referer": str(r.url or login_url),
                "Origin": base_url,
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": csrf},
        )

        final_url = str(r2.url).lower()
        login_failed = (
            "/accounts/login" in final_url
            or "invalid username" in r2.text.lower()
            or "incorrect" in r2.text.lower()
        )
        if not client.cookies.get("sessionid") or login_failed:
            await _update_captcha_login_status(key, "❌ Login Failed — username/password অথবা captcha ভুল")
            return False

        old_session = captcha_sessions.get(key)
        captcha_sessions[key] = {"client": client, "base_url": base_url}
        if isinstance(old_session, dict) and old_session.get("client") is not client:
            try:
                await old_session["client"].aclose()
            except Exception:
                pass
        await _update_captcha_login_status(key, "✅ Active & Fetching")
        return True

    except Exception as e:
        await _update_captcha_login_status(key, f"❌ Error: {str(e)[:50]}")
        try:
            await client.aclose()
        except Exception:
            pass
        return False


async def fetch_greennews_data(key: str):
    """লগইন করা সেশন দিয়ে Green Panel এর JSON records API পড়ে (fallback: HTML table)।
    Returns (results, raw_text) — results = [{"number","message","otp"}, ...]।
    Raises Exception("Session expired") যদি সেশন মৃত হয়ে যায়।"""
    session_info = captcha_sessions.get(key)
    if not isinstance(session_info, dict) or "client" not in session_info:
        raise Exception("Session expired")
    client: httpx.AsyncClient = session_info["client"]
    base_url = session_info.get("base_url") or _gp_base_url(CAPTCHA_PANELS.get(key, {}).get("login_url") or "")

    records_url = f"{base_url}{_GP_RECORDS_PATH}"
    r = await client.get(
        records_url,
        params={"page": 1, "page_size": 100},
        headers={**_GP_AJAX_HEADERS, "Referer": f"{base_url}{_GP_INCOMING_PATH}", "Accept": "application/json"},
    )

    final_url = str(r.url).lower()
    if r.status_code in (401, 403) or "/accounts/login" in final_url:
        raise Exception("Session expired")

    if r.status_code < 400:
        content_type = r.headers.get("Content-Type", "").lower()
        is_json = "json" in content_type or r.text.lstrip().startswith(("[", "{"))
        if is_json:
            rows = _gp_json_rows_to_dicts(r.text)
            results = [res for res in (_gp_dict_row_to_result(row) for row in rows) if res]
            return results, r.text

    # ── Fallback: server-rendered incoming page (HTML table) ──
    r2 = await client.get(
        f"{base_url}{_GP_INCOMING_PATH}",
        headers={**_GP_BASE_HEADERS, "Referer": f"{base_url}/"},
    )
    final_url2 = str(r2.url).lower()
    if r2.status_code in (401, 403) or "/accounts/login" in final_url2:
        raise Exception("Session expired")

    results = _gp_parse_html_table(r2.text)
    return results, r2.text


async def captcha_panel_monitor(key: str):
    """One instance runs per Auto Captcha Panel, sharing the same 'numbers'
    stock table as the token-based panels above — whichever panel's data
    shows the SMS first claims the number."""
    tag = f"[{CAPTCHA_PANELS.get(key, {}).get('label', key)}CaptchaMonitor]"
    print(f"{tag} Auto Captcha Panel monitor loop started.")

    while True:
        if key not in CAPTCHA_PANELS:
            break
        try:
            # 1. Log in if we don't have an active session yet
            if key not in captcha_sessions:
                ok = await attempt_captcha_login(key)
                if not ok:
                    await asyncio.sleep(30)
                    continue

            # 2. Fetch & parse the panel's SMS table FIRST (regardless of whether
            # there's currently anything to watch) — eta status ke live/accurate
            # রাখে (✅ Active & Fetching বা আসল সমস্যা), Test Connection চাপার
            # দরকার ছাড়াই panel list/detail এ দেখা যায়।
            try:
                parsed_data, _ = await fetch_captcha_panel_data(key)
            except Exception as e:
                # Session likely expired — drop it, re-login next loop
                print(f"{tag} Fetch error: {e}")
                captcha_sessions.pop(key, None)
                err_text = str(e)
                if "session expired" in err_text.lower():
                    await _update_captcha_login_status(key, "❌ Session Expired (Retrying...)")
                else:
                    await _update_captcha_login_status(key, f"❌ Fetch Error: {err_text[:60]}")
                await asyncio.sleep(5)
                continue

            # Fetch সফল হয়েছে — status "✅ Active & Fetching" আছে কিনা confirm/refresh করো,
            # যাতে আগের কোনো error status stale থেকে না যায়।
            cur_status = CAPTCHA_PANELS.get(key, {}).get("login_status", "")
            if not cur_status.startswith("✅"):
                await _update_captcha_login_status(key, "✅ Active & Fetching")

            # 3. Get active leased busy Stock Numbers (shared with other panel types),
            # shathe koto ta SMS already forward kora hoyeche shei count-o niye ashi
            async with aiosqlite.connect(DB_FILE) as db:
                async with db.execute(
                    "SELECT phone_number, service, country, assigned_to, sms_seen_count, otp_rate FROM numbers WHERE status = 'busy'"
                ) as cursor:
                    rows = await cursor.fetchall()

            if not rows:
                await asyncio.sleep(10)
                continue

            watched_dict = {row[0]: (row[1], row[2], row[3], row[4] or 0, row[5]) for row in rows}

            # Proti number er SMS gulo group kori. Notun SMS chena hoy purely
            # COUNT die (content/otp na) — tai eki text/OTP baar baar ashleo
            # protita alada SMS hishebe forward hobe, jotobar pathano hobe totobar-i.
            grouped: dict = {}
            for item in parsed_data:
                clean_num = item["number"]
                if clean_num and clean_num in watched_dict:
                    grouped.setdefault(clean_num, []).append(item)

            for clean_num, entries in grouped.items():
                service, country, assigned_user, seen_count, number_rate = watched_dict[clean_num]
                if len(entries) <= seen_count:
                    continue  # kono notun SMS ashe ni ei number e

                for item in entries[seen_count:]:
                    msg = item["message"]
                    otp_str = item["otp"]

                    # OTP successfully received. Status 'busy'-i thake (DELETE kora
                    # hoy na), tai loop eta watch korte thakbe — porer SMS gulo-o
                    # forward hobe. 30 min busy-timeout expire hole otp_received=1
                    # thakle number-ta 'retired' e chole jabe.
                    check_daily_reset()
                    state.daily_otp_count += 1
                    await record_panel_otp(key)

                    base_rate = get_effective_earning_rate(service, country, stock_only=True)
                    earned = (float(number_rate) if number_rate is not None else base_rate)
                    if earned < 0:
                        earned = 0.0
                    if earned:
                        await add_balance(assigned_user, earned)
                        await credit_referral_commission(assigned_user, earned)

                    if assigned_user not in state.otp_history:
                        state.otp_history[assigned_user] = []
                    state.otp_history[assigned_user].append({
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "ts":        time.time(),
                        "phone":     clean_num,
                        "service":   service.upper(),
                        "otp":       otp_str
                    })
                    await record_user_otp(assigned_user, service, earned)

                    # Keep the original country parts for the group forward card.
                    flag_emoji, short_code = get_country_parts(clean_num)
                    country_name = country_name_for_number(clean_num)
                    country_flag = country_flag_emoji(country_name)
                    if country_flag == "🏳️":
                        country_flag, _country_short = get_country_parts(clean_num)
                    svc_icon = svc_tag_icon(service) or svc_display_name(service)
                    current_balance = await get_balance(assigned_user)
                    otp_text = (
                        f"🏳 <code>{html.escape(str(clean_num))}</code> 💰<b>{earned:.2f}Tk</b>\n"
                        f"{svc_icon} {html.escape(svc_display_name(service))} · {country_flag} "
                        f"{html.escape(country_name)}\n"
                        f"🏦 Balance: <b>{current_balance:.2f}Tk</b>"
                    )
                    otp_markup = markup([btn(otp_str, E_OTP_KEY, copy_text=otp_str, style="danger")])

                    # Forward card er icon ALADA FORWARD_CARD_SERVICE_EMOJIS
                    # dictionary theke ashe (svc_icon theke independent).
                    fwd_icon = fwd_svc_icon(service)
                    lang = detect_language(msg)
                    masked = hide_number(clean_num)
                    country_flag_label = country_flag_emoji(country_name)
                    f_text = (
                        f"☎️ <b>{html.escape(forward_mask_number(clean_num))}</b>\n"
                        f"{fwd_icon} <b>{html.escape(svc_display_name(service))}</b> · "
                        f"{country_flag_label} <b>{html.escape(country_name)}</b>"
                    )
                    f_markup = markup(
                        [btn(bold_button(otp_str), E_FWD_OTP, copy_text=otp_str, style="danger")],
                        [btn(bold_button("Channel"), E_FWD_BELL, url=FWD_CHANNEL_URL, style="danger"),
                         btn(bold_button("Get Number"), E_RK_GET_NUM, url=FWD_GET_NUMBER_URL, style="__status_red__")]
                    )

                    async def _send_user():
                        try:
                            await asyncio.wait_for(
                                bot.send_message(chat_id=assigned_user, text=otp_text, reply_markup=otp_markup),
                                timeout=8.0
                            )
                        except Exception as err:
                            print(f"{tag} User send error: {err}")

                    async def _send_group():
                        try:
                            target_chat = OTP_GROUP_ID
                            if not target_chat:
                                print(f"{tag} Forward skipped: OTP_GROUP_ID is not configured")
                                return
                            await send_dynamic_otp_card(target_chat, clean_num, country_name, service, otp_str, f_text, f_markup)
                        except Exception as err:
                            print(f"{tag} Forward error: {err}")

                    await asyncio.gather(_send_user(), _send_group())

                # Ei number er shob notun SMS forward howar por total count DB te
                # update kore dei, r otp_received=1 mark kori (permanent retire logic)
                async with aiosqlite.connect(DB_FILE) as db:
                    await db.execute(
                        "UPDATE numbers SET sms_seen_count = ?, otp_received = 1 WHERE phone_number = ? AND service = ? AND assigned_to = ?",
                        (len(entries), clean_num, service, assigned_user)
                    )
                    await db.commit()

                # Refresh only after the write connection is fully closed so
                # the committed otp_received=1 value is immediately visible.
                await _refresh_number_card_after_otp(assigned_user, service, country)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"{tag} Loop error: {e}")

        await asyncio.sleep(10)

# ================= HANDLERS =================

async def _capture_referral_payload(uid: int, text: str):
    """Capture /start ref_<id> and persist it until registration/verification finishes."""
    if not text:
        return
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].startswith("ref_"):
        return
    try:
        referrer_id = int(parts[1][4:])
    except (ValueError, TypeError):
        return
    if not referrer_id or referrer_id == uid:
        return

    # First valid referral wins; don't overwrite attribution on later /start taps.
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT 1 FROM referrals WHERE user_id = ?", (uid,)) as cur:
            if await cur.fetchone():
                state.pending_referrer.pop(uid, None)
                return
        async with db.execute("SELECT referrer_id FROM pending_referrals WHERE user_id = ?", (uid,)) as cur:
            existing = await cur.fetchone()
        if existing:
            state.pending_referrer[uid] = int(existing[0])
            return
        now = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "INSERT OR IGNORE INTO pending_referrals (user_id, referrer_id, created_at) VALUES (?, ?, ?)",
            (uid, referrer_id, now),
        )
        await db.commit()
    state.pending_referrer[uid] = referrer_id

async def _finalize_referral_if_new(uid: int, is_new: bool):
    """Finalize a pending referral only for a genuinely new registered user."""
    if not is_new:
        # Existing users are deliberately not counted as new referrals.
        state.pending_referrer.pop(uid, None)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("DELETE FROM pending_referrals WHERE user_id = ?", (uid,))
            await db.commit()
        return

    referrer_id = state.pending_referrer.get(uid)
    if referrer_id is None:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT referrer_id FROM pending_referrals WHERE user_id = ?", (uid,)) as cur:
                row = await cur.fetchone()
                referrer_id = int(row[0]) if row else None

    if not referrer_id:
        return
    registered = await set_referrer(uid, int(referrer_id))
    # Clear pending state after success, or if this user already had a referral.
    if registered or await get_referrer(uid):
        state.pending_referrer.pop(uid, None)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("DELETE FROM pending_referrals WHERE user_id = ?", (uid,))
            await db.commit()

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    uid = m.from_user.id

    if is_banned(uid):
        await m.answer(f"{ce(E_BROADCAST_FAIL, '❌')} You are banned.")
        return

    await _capture_referral_payload(uid, m.text)

    # Remove any old Join Channel / Join OTP Group prompt first, including one
    # whose message id was saved before a bot restart.
    await _delete_persisted_force_join_message(uid)

    if uid in state.verified_users:
        # Never trust stale in-memory verification after restart.
        if await is_joined(uid):
            # Keep referral finalization consistent in this already-verified path too.
            is_new = add_user(users_db, m.from_user, uid)
            await _finalize_referral_if_new(uid, is_new)
            state.rk_visible.add(uid)
            await send_with_reply_kb(uid, main_menu_text(m.from_user), uid)
            return
        state.verified_users.discard(uid)
        state.rk_visible.discard(uid)

    # After a restart, verified_users is empty. Check Telegram directly before
    # showing Join buttons, so an already-joined user goes straight to the menu.
    if await is_joined(uid):
        state.verified_users.add(uid)
        is_new = add_user(users_db, m.from_user, uid)
        await _finalize_referral_if_new(uid, is_new)
        state.rk_visible.add(uid)
        await send_with_reply_kb(uid, main_menu_text(m.from_user), uid)
        return

    # Force Join off or no channels configured — skip the prompt entirely.
    if not force_join_enabled() or not state.force_join_channels:
        state.verified_users.add(uid)
        is_new = add_user(users_db, m.from_user, uid)
        await _finalize_referral_if_new(uid, is_new)
        state.rk_visible.add(uid)
        await send_with_reply_kb(uid, main_menu_text(m.from_user), uid)
        return

    join_list = "   ".join(f"{ce(E_JOIN_CHANNEL, '📢')} {ch['label']}" for ch in state.force_join_channels.values())
    join_msg = await m.answer(
        f"{ce(E_JOIN_WAVE, '👋')} <b>Hi {m.from_user.first_name}!</b> Join to continue:\n"
        f"{join_list}",
        reply_markup=join_verify_keyboard()
    )
    # Keep the prompt message id so it can be deleted automatically as soon
    # as the user joins the final required channel/group.
    state.force_join_messages[uid] = join_msg.message_id
    await _save_force_join_message_id(uid, join_msg.message_id)

    joined = await is_joined(uid)
    if joined:
        state.verified_users.add(uid)
        is_new = add_user(users_db, m.from_user, uid)
        await _finalize_referral_if_new(uid, is_new)
        state.rk_visible.add(uid)
        # Verification is completed silently; remove the verification message
        # instead of leaving a visible "Verified!" message in the chat.
        await _delete_persisted_force_join_message(uid)
        await send_with_reply_kb(uid, main_menu_text(m.from_user), uid)

@dp.callback_query(F.data == "menu")
async def show_menu(c: types.CallbackQuery):
    try:
        await c.answer()
        await c.message.delete()
    except Exception as e:
        print(f"Error closing menu: {e}")

@dp.callback_query(F.data == "noop")
async def noop_cb(c: types.CallbackQuery):
    try:
        await c.answer()
    except Exception:
        pass

@dp.callback_query(F.data == "close_menu")
async def close_menu_cb(c: types.CallbackQuery):
    try:
        await c.answer()
        await c.message.delete()
    except Exception as e:
        print(f"Error deleting menu: {e}")

# ================= STOCK FLOW (SMS HADI STOCK) =================

def normal_service_label(service_name: str) -> str:
    """Return only the service name.
    The Premium/custom emoji is attached to the inline button via
    icon_custom_emoji_id, so the label itself stays clean.
    """
    return str(service_name or "").strip()

async def _stock_menu_inline_kb() -> dict:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT service, COUNT(*) FROM numbers WHERE status = 'available' GROUP BY service") as cursor:
            rows = await cursor.fetchall()
    
    buttons = []
    for svc_name, count in rows:
        # get_svc_icon() ekhon kokhono None return kore na — custom emoji
        # na thakle (jemon Bolt, Facebook2, NEW FB, Tinder5) o default 🔷
        # emoji e fallback kore, tai button-e icon field always thake ar
        # service country / number card flow break hoy na.
        icon = get_svc_icon(svc_name)
        # Service button-এ কোনো rate/টাকার amount দেখানো হবে না।
        # Rate শুধু stock/country card-এ দেখানো হবে।
        rate_text = ""
        # Service-wise native Telegram button colors:
        # WhatsApp = red, Telegram = green; the remaining services use
        # the other available native styles in a deterministic rotation.
        svc_key = str(svc_name).strip().lower()
        if svc_key == "whatsapp":
            service_style = "danger"
        elif svc_key == "telegram":
            service_style = "danger"
        elif svc_key == "facebook":
            service_style = "danger"
        elif svc_key in {"instagram", "newfb"}:
            service_style = "danger"
        elif svc_key == "discord":
            service_style = "danger"
        else:
            service_style = ("danger", "danger", "danger")[sum(ord(ch) for ch in svc_key) % 3]
        # Use Telegram Bot API's native Premium custom-emoji button icon.
        # Bot API 9.4 supports icon_custom_emoji_id on InlineKeyboardButton.
        # Keep the label clean (no duplicate Unicode emoji). If a configured
        # ID is unavailable, Telegram may reject it; get_svc_icon() currently
        # returns the configured official/custom/default ID.
        service_button = {
            "text": normal_service_label(svc_name).upper(),
            "callback_data": f"hadi_svc_{svc_name}",
            "style": service_style,
        }
        if str(icon or "").strip().isdigit():
            service_button["icon_custom_emoji_id"] = str(icon).strip()
        else:
            # Unicode fallback only when there is no usable custom emoji ID.
            svc_key = str(svc_name).strip().lower()
            picker_icons = {
                "facebook": "📕", "whatsapp": "🛡", "telegram": "✈️",
                "instagram": "📸", "discord": "🎮", "newfb": "📸"
            }
            main_key = detect_main_service(svc_name)
            service_button["text"] = f"{picker_icons.get(svc_key, picker_icons.get(main_key, '✨'))} {normal_service_label(svc_name)}"
        buttons.append(service_button)

    if not buttons:
        return None

    # Match the requested Get Number layout: two service buttons per row,
    # followed by a full-width Back button.
    service_rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    service_rows.append([
        btn("↩ Back", E_TOOL_BACKBUTTON, callback_data="menu", style="danger")
    ])
    return {"inline_keyboard": service_rows}

@dp.callback_query(F.data == "menu_stock_home")
async def cb_menu_stock_home(c: types.CallbackQuery):
    try:
        await c.answer()
    except Exception:
        pass

    kb = await _stock_menu_inline_kb()
    if not kb:
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} No stock available",
            markup([btn("↩️ Back to Main", E_TOOL_BACKBUTTON, callback_data="menu", style="danger")])
        )
        return
    
    await safe_edit(
        c.message,
        "📍 <b>Select a service from the list</b>",
        kb
    )

async def _clear_previous_service_flow(uid: int, keep_message_id: int | None = None):
    """Remove the previous Get Number service/number message for this user.
    This prevents multiple service panels from remaining visible when the user
    starts another service flow. The current message can be preserved by id.
    """
    info = state.service_flow_messages.get(uid)
    if not info:
        return
    old_chat = info.get("chat_id")
    old_mid = info.get("message_id")
    if old_mid and old_mid != keep_message_id:
        try:
            await bot.delete_message(old_chat, old_mid)
        except Exception:
            # Already deleted/expired/non-deletable messages are harmless.
            pass
    if keep_message_id is None:
        state.service_flow_messages.pop(uid, None)


def _remember_service_flow(uid: int, message: types.Message, service: str):
    state.service_flow_messages[uid] = {
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "service": str(service),
    }


async def _release_other_stock_services(chat_id: int, keep_service: str | None = None):
    """Release busy stock numbers belonging to another service.
    A user may keep an active lease only for the currently selected service.
    """
    async with aiosqlite.connect(DB_FILE) as db:
        if keep_service:
            await db.execute(
                """UPDATE numbers
                   SET status = 'retired',
                       assigned_to = NULL, assign_time = NULL
                 WHERE assigned_to = ? AND status = 'busy' AND service != ?""",
                (chat_id, keep_service),
            )
        else:
            await db.execute(
                """UPDATE numbers
                   SET status = 'retired',
                       assigned_to = NULL, assign_time = NULL
                 WHERE assigned_to = ? AND status = 'busy'""",
                (chat_id,),
            )
        await db.commit()


def _checker_rows_for_service(service_name: str, phone_number: str):
    # Checker buttons are intentionally disabled. Number rows contain only numbers.
    return []


async def _refresh_number_card_after_otp(user_id: int, service_name: str, country_name: str):
    """Refresh the active number card immediately after an OTP arrives.
    Visual status is independent of the internal lease status='busy':
    fresh numbers are green; once otp_received=1 they become red.
    """
    info = state.service_flow_messages.get(user_id)
    if not info:
        return
    if str(info.get("service", "")).strip().lower() != str(service_name).strip().lower():
        return

    try:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                """SELECT phone_number, otp_rate
                   FROM numbers
                   WHERE assigned_to = ? AND service = ? AND status = 'busy'
                   ORDER BY rowid""",
                (user_id, service_name),
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            return

        phone_nums = [r[0] for r in rows]
        selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
        display_country = get_country(phone_nums[0])
        label = svc_tag(service_name)
        card_text, kb = build_hadi_number_card(
            service_name,
            country_name,
            phone_nums,
            display_country,
            label,
            otp_rate=selected_rate,
            user_id=user_id,
        )

        await _tg_client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={
                "chat_id": info["chat_id"],
                "message_id": info["message_id"],
                "text": card_text,
                "parse_mode": "HTML",
                "reply_markup": kb,
            },
            timeout=8.0,
        )
    except Exception as e:
        print(f"[NumberCard] OTP status refresh error: {e}")


@dp.callback_query(F.data.startswith("hadi_svc_"))
async def cb_hadi_svc(c: types.CallbackQuery):
    try:
        await c.answer("Loading...")
    except Exception:
        pass


    svc_name = c.data.split("_", 2)[2]
    uid = c.from_user.id

    # Keep the service flow and show the same SELECT COUNTRY screen used by
    # Change Country, with full-width country buttons and a full-width Back.
    await _clear_previous_service_flow(uid, keep_message_id=c.message.message_id)
    _remember_service_flow(uid, c.message, svc_name)
    await _release_other_stock_services(c.message.chat.id, keep_service=svc_name)

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """SELECT country, MIN(CAST(COALESCE(otp_rate, ?) AS REAL)) AS display_rate
               FROM numbers
               WHERE service = ? AND status = 'available'
                 AND NOT EXISTS (
                    SELECT 1 FROM number_usage_history h
                    WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')
                        = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')
                 )
               GROUP BY country
               ORDER BY country COLLATE NOCASE""",
            (get_stock_rate(svc_name), svc_name)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} No countries available for this service right now.",
            markup([btn("Back", callback_data="menu_stock_home", style="danger")])
        )
        return

    country_buttons = []
    for cname, rate in rows:
        cname = str(cname or "").strip()
        if not cname:
            continue
        cname_escaped = cname.replace(" ", "_")
        try:
            rate_text = f"{float(rate):.2f}Tk"
        except (TypeError, ValueError):
            rate_text = "Rate N/A"
        country_buttons.append({
            "text": f"{country_flag_emoji(cname)} {cname.upper()} · {rate_text.upper()}",
            "callback_data": f"hadi_ctry_{svc_name}_{cname_escaped}"
        })

    # Show two country buttons side by side on each row.
    country_rows = [country_buttons[i:i + 2] for i in range(0, len(country_buttons), 2)]
    country_rows.append([{
        "text": "Back", "callback_data": "menu_stock_home"
    }])

    service_title = html.escape(svc_display_name(svc_name))
    service_icon = svc_tag_icon(svc_name)
    text = f"{svc_tag_icon(svc_name)} <b>{html.escape(svc_display_name(svc_name).upper())}</b>    <b>SELECT COUNTRY</b> 👇👇"
    await safe_edit(c.message, text, {"inline_keyboard": country_rows})


def build_hadi_number_card(service_name, country_name, phone_nums, display_country, label, cc_removed=False, otp_rate=None, user_id=None):
    # HARD UI CAP: a number card may never display more than 3 numbers.
    # This also protects older callers that accidentally pass a larger list.
    phone_nums = list(phone_nums or [])[:3]
    # Number card: always show a visible country flag + the COMPLETE country name.
    # Do not show the stock/OTP rate on this user-facing card.
    detected_country = country_name_for_number(phone_nums[0]) if phone_nums else ''
    full_country = detected_country if detected_country and detected_country != 'Global' else str(country_name or display_country or 'Global').strip()
    flag = country_flag_emoji(full_country)
    if flag == '🏳️' and phone_nums:
        # Fallback to the number-derived Unicode flag when the uploaded country
        # label is formatted differently from the country map.
        digits = re.sub(r'\D', '', str(phone_nums[0]))
        key = _parse_country_cached(digits) if digits else None
        if key:
            iso = _prefix_to_short.get(key)
            if iso and len(iso) == 2:
                flag = unicode_flag(iso)

    # Premium-style compact header: service and country on the same line,
    # then the optional prefix, followed by the OTP waiting status.
    country_line = f"{flag} <b>{html.escape(full_country)}</b>"
    saved_prefix = {}
    if user_id is not None:
        candidate = state.prefix_saved.get(user_id) or {}
        if (str(candidate.get("service", "")).strip().lower() == str(service_name).strip().lower()
                and str(candidate.get("country", "")).strip().lower() == str(full_country).strip().lower()):
            saved_prefix = candidate
    prefix_line = ""
    if saved_prefix.get("prefix"):
        prefix_line = f"{ce(E_RANGE_PHONE, '🔎')} <b>Prefix :</b> <code>{html.escape(str(saved_prefix['prefix']))}</code>\n"

    # Compact, premium-emoji layout matching the requested reference.
    # Keep the information card compact, with the same short padded lines as
    # requested. U+3164 Hangul fillers are used because Telegram HTML may trim
    # ordinary trailing spaces from message text.
    pad = "ㅤㅤㅤㅤ"
    prefix_line = (
        f"{ce(E_RANGE_PHONE, '🔎')} <b>Prefix :</b> <code>{html.escape(str(saved_prefix['prefix']))}</code>{pad}\n"
        if saved_prefix.get("prefix") else ""
    )
    # Show the configured OTP rate on the allocated-number card in both currencies.
    rate_value = None
    if isinstance(otp_rate, dict):
        for _phone in phone_nums:
            try:
                if otp_rate.get(_phone) is not None:
                    rate_value = float(otp_rate[_phone])
                    break
            except (TypeError, ValueError):
                continue
    rate_line = (
        f"💰 <b>OTP Rate: {format_dual_rate(rate_value)}</b>{pad}\n"
        if rate_value is not None else ""
    )
    text = (
        f"{svc_tag_icon(service_name)} <b>{html.escape(svc_display_name(service_name))}</b>   {country_line}{pad}\n"
        f"{prefix_line}"
        f"{rate_line}"
        f"{ce(E_NUM_WAITING, '⏳')} <i>Waiting for OTP</i>{pad}"
    )

    svc_icon_id = get_svc_icon(service_name)
    num_btns = []
    # Keep the number buttons clean: the OTP rate is shown with the service above,
    # not beside each individual number.
    for pn in phone_nums:
        if cc_removed or not is_country_code_enabled(user_id):
            stripped = strip_cc(pn)
            display_num = stripped
            copy_num = re.sub(r"\D", "", stripped)
        else:
            digits = re.sub(r"\D", "", str(pn))
            display_num = f"+{digits}"
            copy_num = digits

        # IMPORTANT: visual color is NOT based on the internal lease status.
        # A freshly leased number is internally status='busy' so the OTP
        # monitor can watch it, but it must still appear GREEN to the user.
        # It turns RED only after the first OTP is actually received
        # (otp_received=1), or if the number is permanently retired.
        number_status = "available"
        otp_received = 0
        try:
            import sqlite3 as _sqlite3
            with _sqlite3.connect(DB_FILE) as _db:
                _row = None
                if user_id is not None:
                    _row = _db.execute(
                        "SELECT status, COALESCE(otp_received, 0) FROM numbers WHERE phone_number = ? AND service = ? AND assigned_to = ? ORDER BY rowid DESC LIMIT 1",
                        (str(pn), service_name, int(user_id)),
                    ).fetchone()
                if not _row:
                    _row = _db.execute(
                        "SELECT status, COALESCE(otp_received, 0) FROM numbers WHERE phone_number = ? AND service = ? ORDER BY rowid DESC LIMIT 1",
                        (str(pn), service_name),
                    ).fetchone()
            if _row:
                number_status = (_row[0] or "available").strip().lower()
                otp_received = int(_row[1] or 0)
        except Exception:
            number_status = "available"
            otp_received = 0

        # FINAL NUMBER COLOR RULE:
        # Before OTP = default/white; after OTP = red.
        # Internal status='busy' is deliberately ignored here because it is
        # only the temporary lease state used by the OTP monitor.
        is_used = (otp_received == 1) or (number_status == "retired")
        number_style = "__status_red__" if otp_received == 1 else None

        # Keep the number button clean: no status dot or clipboard emoji.
        # Telegram's native green/red button background still indicates status.
        button_text = str(display_num)
        num_btns.append(
            btn(
                button_text,
                svc_icon_id,
                copy_text=copy_num,
                style=number_style,
                suppress_auto_emoji=True,
            )
        )

    # FORCE one number per row. Never combine number buttons horizontally.
    # Telegram receives exactly: [[number1], [number2], [number3]].
    rows = [[item] for item in num_btns]

    # Compact card width: keep each line at the short length requested by the
    # user instead of padding it toward the full-width number buttons. Telegram
    # controls the actual message bubble width, so only a small, fixed visual
    # spacer is used. This leaves number-button rows and callbacks unchanged.
    # Each line above already includes four U+3164 fillers; do not add any more.

    cname_escaped = country_name.replace(" ", "_")
    rows.append([btn("Change Number", E_TOOL_CHANGENUMBER, premium_emoji=True, callback_data=f"hadi_chgnum_{service_name}_{cname_escaped}", style="danger")])
    prefix_is_on = bool(user_id is not None and str(state.prefix_saved.get(user_id, {}).get("service", "")).strip().lower() == str(service_name).strip().lower())
    rows.append([
        btn("Change Country", suppress_auto_emoji=True, callback_data=f"hadi_chgctry_{service_name}", style="danger"),
        btn("🔎 Prefix", E_RANGE_PHONE, callback_data=f"hadi_prefix_{service_name}", style="__status_red__" if prefix_is_on else None),
    ])
    rows.append([btn("REMOVE COUNTRY CODE" if is_country_code_enabled(user_id) else "ADD COUNTRY CODE", suppress_auto_emoji=True, callback_data="toggle_country_code", style="__status_red__" if is_country_code_enabled(user_id) else None)])
    rows.append([btn("OTP Group", E_MENU_OTPGROUP, url=OTP_GROUP_URL, style="danger")])
    return text, {"inline_keyboard": rows}

async def _show_prefix_input(c: types.CallbackQuery, service_name: str, force_change: bool = False):
    """Switch the existing card into Prefix input mode."""
    uid = c.from_user.id
    country_name = ""
    try:
        card_text = str(getattr(c.message, "text", "") or "")
        m = re.search(r"<b>\s*Country\s*:\s*</b>.*?<b>([^<]+)</b>", card_text, re.I | re.S)
        if m:
            country_name = html.unescape(m.group(1)).strip()
    except Exception:
        country_name = ""

    saved = state.prefix_saved.get(uid) or {}
    if (not force_change) and saved and str(saved.get("service", "")).strip().lower() == service_name.strip().lower():
        # Prefix is ON. Pressing the same Prefix button toggles it OFF.
        state.prefix_saved.pop(uid, None)
        state.prefix_filter_active.pop(uid, None)
        state.prefix_filter_pending.pop(uid, None)
        try:
            await c.answer("Prefix OFF")
        except Exception:
            pass
        try:
            async with aiosqlite.connect(DB_FILE) as db:
                async with db.execute(
                    "SELECT phone_number, otp_rate, country FROM numbers WHERE assigned_to = ? AND service = ? AND status = 'busy' ORDER BY rowid LIMIT 3",
                    (c.message.chat.id, service_name),
                ) as cur:
                    rows = await cur.fetchall()
            if rows:
                phones = [r[0] for r in rows]
                rates = {r[0]: r[1] for r in rows if r[1] is not None}
                country = str(rows[0][2] or country_name or "").strip()
                text_card, kb = build_hadi_number_card(service_name, country, phones, country, svc_tag(service_name), otp_rate=rates, user_id=uid)
                await safe_edit(c.message, text_card, kb)
        except Exception as e:
            print(f"[Prefix] Toggle OFF card refresh failed: {e}")
        return

    state.prefix_filter_active.pop(uid, None)
    state.prefix_filter_pending[uid] = {
        "chat_id": c.message.chat.id,
        "message_id": c.message.message_id,
        "service": service_name,
        "country": country_name,
    }
    keyboard = {"inline_keyboard": [[btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="hadi_prefix_back", style="danger")]]}
    try:
        await _tg_client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={
                "chat_id": c.message.chat.id,
                "message_id": c.message.message_id,
                "text": (
                    f"{ce(E_RANGE_PHONE, '🔎')} <b>Prefix Filter</b>\n\n"
                    f"Max: 5 digits after Country code \n"
                    f"Example: <code>88017123</code>\n\n"
                    f"Service: <b>{html.escape(svc_display_name(service_name))}</b>"
                ),
                "parse_mode": "HTML",
                "reply_markup": keyboard,
            },
            timeout=8.0,
        )
    except Exception as e:
        print(f"[Prefix] Open input failed: {e}")


# Prefix Change Number cooldown: one accepted change every 5 seconds per user.
PREFIX_CHANGE_LAST: dict[int, float] = {}
PREFIX_CHANGE_LOCKS: dict[int, asyncio.Lock] = {}


@dp.callback_query(F.data.regexp(r"^hadi_prefix_change_.+"))
async def cb_hadi_prefix_change(c: types.CallbackQuery):
    uid = c.from_user.id
    now = time.monotonic()
    last_change = PREFIX_CHANGE_LAST.get(uid, 0.0)
    remaining = 5.0 - (now - last_change)
    if remaining > 0:
        try:
            await c.answer(f"⏳ Please wait {remaining:.1f}s before changing again.", show_alert=True)
        except Exception:
            pass
        return

    lock = PREFIX_CHANGE_LOCKS.setdefault(uid, asyncio.Lock())
    if lock.locked():
        try:
            await c.answer("🔄 Number change is already in progress...", show_alert=True)
        except Exception:
            pass
        return

    async with lock:
        now = time.monotonic()
        last_change = PREFIX_CHANGE_LAST.get(uid, 0.0)
        remaining = 5.0 - (now - last_change)
        if remaining > 0:
            try:
                await c.answer(f"⏳ Please wait {remaining:.1f}s before changing again.", show_alert=True)
            except Exception:
                pass
            return

        service_name = str(c.data[len("hadi_prefix_change_"):]).strip()
        if not service_name:
            try: await c.answer()
            except Exception: pass
            return

        # Start the 5-second cooldown only when the change action is accepted.
        PREFIX_CHANGE_LAST[uid] = time.monotonic()
        try: await c.answer()
        except Exception: pass
        await _show_prefix_input(c, service_name, force_change=True)


@dp.callback_query(F.data.regexp(r"^hadi_prefix_clear_.+"))
async def cb_hadi_prefix_clear(c: types.CallbackQuery):
    """Clear Prefix and return directly to this service's country selection."""
    uid = c.from_user.id
    service_name = str(c.data[len("hadi_prefix_clear_"):]).strip()
    if not service_name:
        return

    try:
        await c.answer("Prefix cleared")
    except Exception:
        pass

    chat_id = c.message.chat.id

    state.prefix_saved.pop(uid, None)
    state.prefix_filter_active.pop(uid, None)
    state.prefix_filter_pending.pop(uid, None)

    # Release the current Prefix lease before reopening the service.
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "UPDATE numbers SET status = 'retired', assigned_to = NULL, "
                "assign_time = NULL "
                "WHERE assigned_to = ? AND service = ? AND status = 'busy'",
                (chat_id, service_name),
            )
            await db.commit()
    except Exception as e:
        print(f"[Prefix] Clear release failed: {e}")

    try:
        await safe_edit(c.message, "🔄 <b>Prefix cleared...</b>")
        await asyncio.sleep(0.35)
    except Exception:
        pass

    # Rebuild the normal service country-selection UI.
    try:
        await _clear_previous_service_flow(uid, keep_message_id=c.message.message_id)
        _remember_service_flow(uid, c.message, service_name)

        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                """SELECT country, MAX(COALESCE(otp_rate, ?)) AS display_rate
                   FROM numbers
                   WHERE service = ? AND status = 'available'
                   GROUP BY country
                   ORDER BY country""",
                (get_stock_rate(service_name), service_name),
            ) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            await safe_edit(
                c.message,
                f"{ce(E_BROADCAST_FAIL, '❌')} No stock for this service",
                markup([
                    btn("↩️ Back", E_TOOL_BACKBUTTON,
                        callback_data="menu_stock_home", style="danger")
                ]),
            )
            return

        country_buttons = []
        for cname, db_rate in rows:
            cname = str(cname or "").strip()
            if not cname:
                continue
            cname_escaped = cname.replace(" ", "_")
            try:
                rate = float(db_rate or get_stock_rate(service_name))
            except (TypeError, ValueError):
                rate = get_stock_rate(service_name)
            if rate <= 0:
                rate = get_stock_rate(service_name)

            country_buttons.append({
                "text": f"{country_flag_emoji(cname)} {cname.upper()} · {format_dual_rate(rate)}",
                "callback_data": f"hadi_ctry_{service_name}_{cname_escaped}",
            })

        # Keep every country-selection screen consistent: two countries per row.
        country_rows = []
        for i in range(0, len(country_buttons), 2):
            pair = country_buttons[i:i + 2]
            if len(pair) == 2:
                target_len = max(len(str(item.get("text", ""))) for item in pair)
                for item in pair:
                    item["text"] = str(item.get("text", "")).ljust(target_len)
            country_rows.append(pair)

        country_rows.append([{
            "text": "BACK",
            "callback_data": "menu_stock_home",
        }])

        text_card = f"{svc_tag_icon(service_name)} <b>{html.escape(svc_display_name(service_name).upper())}</b>    <b>SELECT COUNTRY</b> 👇👇"
        await safe_edit(c.message, text_card, {"inline_keyboard": country_rows})

    except Exception as e:
        print(f"[Prefix] Clear -> service screen failed: {e}")
        try:
            await safe_edit(
                c.message,
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Could not load service.</b>",
                markup([
                    btn("Retry", E_TOOL_CHANGENUMBER,
                        callback_data=f"hadi_svc_{service_name}", style="danger"),
                    btn("Back", E_TOOL_BACKBUTTON,
                        callback_data="menu_stock_home", style="danger"),
                ]),
            )
        except Exception:
            pass


@dp.callback_query(F.data.regexp(r"^hadi_prefix_cancel_.+"))
async def cb_hadi_prefix_cancel(c: types.CallbackQuery):
    try: await c.answer()
    except Exception: pass
    uid = c.from_user.id
    service_name = str(c.data[len("hadi_prefix_cancel_"):]).strip()
    state.prefix_filter_pending.pop(uid, None)
    state.prefix_filter_active.pop(uid, None)
    chat_id = c.message.chat.id
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT phone_number, otp_rate FROM numbers WHERE assigned_to = ? AND service = ? AND status = 'busy' ORDER BY rowid",
            (chat_id, service_name)
        ) as cur:
            rows = await cur.fetchall()
    if rows:
        phone_nums = [r[0] for r in rows]
        selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
        country_name = get_country(phone_nums[0])
        text, kb = build_hadi_number_card(service_name, country_name, phone_nums, country_name, svc_tag(service_name), otp_rate=selected_rate, user_id=uid)
        await safe_edit(c.message, text, kb)


@dp.callback_query(F.data.regexp(r"^hadi_prefix_(?!back(?:$|_)|more$).+"))
async def cb_hadi_prefix(c: types.CallbackQuery):
    """Open Prefix input using the service encoded directly in callback_data."""
    uid = c.from_user.id
    try:
        await c.answer("Prefix selected")
    except Exception:
        pass

    service_name = str(c.data[len("hadi_prefix_"):]).strip()
    if not service_name:
        try: await c.answer("Service not found.", show_alert=True)
        except Exception: pass
        return
    await _show_prefix_input(c, service_name)


@dp.callback_query(F.data == "hadi_prefix_back")
async def cb_hadi_prefix_back(c: types.CallbackQuery):
    """Leave Prefix mode and immediately switch back to Random Number mode."""
    uid = c.from_user.id
    chat_id = c.message.chat.id

    flow = state.service_flow_messages.get(uid) or {}
    service_name = str(flow.get("service") or "").strip()
    if not service_name:
        active = state.prefix_filter_active.get(uid) or state.prefix_saved.get(uid) or {}
        service_name = str(active.get("service") or "").strip()

    if not service_name:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                "SELECT DISTINCT service FROM numbers WHERE assigned_to = ? AND status = 'busy' ORDER BY service LIMIT 2",
                (chat_id,),
            ) as cur:
                svc_rows = await cur.fetchall()
        if len(svc_rows) == 1:
            service_name = str(svc_rows[0][0]).strip()

    if not service_name:
        try:
            await c.answer("Active service not found.", show_alert=True)
        except Exception:
            pass
        return

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT phone_number, country FROM numbers WHERE assigned_to = ? AND service = ? AND status = 'busy' ORDER BY rowid LIMIT 1",
            (chat_id, service_name),
        ) as cur:
            current = await cur.fetchone()

    country_name = str((current[1] if current else "") or "").strip()
    if not country_name and current:
        country_name = get_country(current[0])

    state.prefix_saved.pop(uid, None)
    state.prefix_filter_active.pop(uid, None)
    state.prefix_filter_pending.pop(uid, None)

    if not country_name:
        try:
            await c.answer("Country not found.", show_alert=True)
        except Exception:
            pass
        return

    # Back is an explicit mode switch. Do not let the previous Prefix
    # change cooldown block the first Random Number allocation.
    NUMBER_CHANGE_LAST.pop(chat_id, None)
    PREFIX_CHANGE_LAST.pop(uid, None)

    try:
        await c.answer("🔄 Switching to Random Number...")
    except Exception:
        pass

    fake_data = f"hadi_chgnum_{service_name}_{country_name.replace(' ', '_')}"
    try:
        normal_change = c.model_copy(update={"data": fake_data})
    except Exception:
        normal_change = c
        try:
            normal_change.data = fake_data
        except Exception:
            pass
    await cb_hadi_chgnum(normal_change)


@dp.callback_query(F.data == "toggle_country_code")
async def cb_toggle_country_code(c: types.CallbackQuery):
    """Toggle country-code display and immediately rebuild the active number card.

    The old implementation only edited the existing keyboard.  That could
    leave the UI stale until Change Number rebuilt the card.  Re-read the
    currently assigned numbers and render the whole card immediately instead.
    """
    uid = c.from_user.id
    chat_id = c.message.chat.id
    new_state = not is_country_code_enabled(uid)
    await set_country_code_enabled(new_state, uid)

    try:
        await c.answer(f"Country code: {'ON' if new_state else 'OFF'}")
    except Exception:
        pass

    # Find the service belonging to this active number card.
    flow = state.service_flow_messages.get(uid) or {}
    service_name = str(flow.get("service") or "").strip()

    async with aiosqlite.connect(DB_FILE) as db:
        if service_name:
            async with db.execute(
                "SELECT phone_number, otp_rate, country FROM numbers "
                "WHERE assigned_to = ? AND service = ? AND status = 'busy' "
                "ORDER BY rowid LIMIT 3",
                (chat_id, service_name),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT phone_number, otp_rate, service, country FROM numbers "
                "WHERE assigned_to = ? AND status = 'busy' "
                "ORDER BY rowid LIMIT 3",
                (chat_id,),
            ) as cur:
                rows = await cur.fetchall()

    if not rows:
        # Nothing active: still acknowledge the setting; next number card will
        # use the persisted setting from bot_settings.
        return

    if service_name:
        phone_nums = [r[0] for r in rows]
        selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
        country_name = str(rows[0][2] or "").strip()
    else:
        phone_nums = [r[0] for r in rows]
        selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
        service_name = str(rows[0][2] or "").strip()
        country_name = str(rows[0][3] or "").strip()

    # Always use the standard card builder so Prefix state, country-code state,
    # button styles and the 3-number hard cap stay consistent.
    text, kb = build_hadi_number_card(
        service_name,
        country_name,
        phone_nums[:3],
        country_name,
        svc_tag(service_name),
        otp_rate=selected_rate,
        user_id=uid,
    )
    await safe_edit(c.message, text, kb)


@dp.callback_query(F.data == "hadi_prefix_more")
async def cb_hadi_prefix_more(c: types.CallbackQuery):
    """Handle Prefix > Change Number with a real 5-second per-user cooldown."""
    uid = c.from_user.id
    lock = PREFIX_CHANGE_LOCKS.setdefault(uid, asyncio.Lock())

    if lock.locked():
        try:
            await c.answer("🔄 Number change is already in progress...", show_alert=True)
        except Exception:
            pass
        return

    async with lock:
        now = time.monotonic()
        last_change = PREFIX_CHANGE_LAST.get(uid, 0.0)
        remaining = 5.0 - (now - last_change)
        if remaining > 0:
            try:
                await c.answer(
                    f"⏳ Please wait {remaining:.1f}s before changing again.",
                    show_alert=True,
                )
            except Exception:
                pass
            return

        # The actual allocation handler records the timestamp only after a
        # successful batch allocation. This prevents a failed/empty request
        # from consuming the user's cooldown.
        await _cb_hadi_prefix_more_impl(c)


async def _cb_hadi_prefix_more_impl(c: types.CallbackQuery):
    """Allocate the next 3 prefix-matching numbers.

    Every number shown through Prefix is immediately recorded in
    number_usage_history, exactly like normal Change Number. A phone number
    can therefore be used only once across both Prefix and normal flows.
    """

    uid = c.from_user.id

    # Prefix -> Change Number shares the same 5-second cooldown as the
    # normal Change Number button. A second tap during the cooldown only
    # shows a Telegram popup and does not allocate new numbers.
    now = time.monotonic()
    last_change = NUMBER_CHANGE_LAST.get(uid, 0.0)
    remaining = 5.0 - (now - last_change)
    if remaining > 0:
        try:
            await c.answer(
                f"⏳ Please wait {remaining:.1f}s before changing again.",
                show_alert=True,
            )
        except Exception:
            pass
        return

    try:
        await c.answer("Loading 3 more numbers...")
    except Exception:
        pass

    active = state.prefix_filter_active.get(uid)
    if not active:
        await c.answer("⚠️ Prefix filter expired. Press Prefix again.", show_alert=True)
        return

    service_name = active["service"]
    prefix = active["prefix"]
    chat_id = active["chat_id"]
    message_id = active["message_id"]

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("BEGIN IMMEDIATE")
        country_name = str(active.get("country") or "").strip()
        if country_name:
            query = (
                "SELECT phone_number, otp_rate FROM numbers "
                "WHERE service = ? AND country = ? AND status = 'available' "
                "AND REPLACE(REPLACE(REPLACE(phone_number, '+', ''), ' ', ''), '-', '') LIKE ? "
                "AND NOT EXISTS (SELECT 1 FROM number_usage_history h "
                "                WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')) "
                "ORDER BY RANDOM() LIMIT 3"
            )
            params = (service_name, country_name, prefix + "%")
        else:
            query = (
                "SELECT phone_number, otp_rate FROM numbers "
                "WHERE service = ? AND status = 'available' "
                "AND REPLACE(REPLACE(REPLACE(phone_number, '+', ''), ' ', ''), '-', '') LIKE ? "
                "AND NOT EXISTS (SELECT 1 FROM number_usage_history h "
                "                WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')) "
                "ORDER BY RANDOM() LIMIT 3"
            )
            params = (service_name, prefix + "%")

        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

        if not rows:
            await db.rollback()
            # Prefix stock is exhausted. Show the result in the main bot card
            # instead of leaving the user on the previous numbers.
            try:
                await c.answer("Prefix No More 🚫", show_alert=True)
            except Exception:
                pass

            no_more_kb = {
                "inline_keyboard": [
                    [btn("↩️ Back", E_TOOL_BACKBUTTON,
                         callback_data="hadi_prefix_back", style="danger")],
                ]
            }
            try:
                await safe_edit_by_ids(
                    chat_id,
                    message_id,
                    (
                        f"{ce(E_RANGE_PHONE, '🔎')} <b>Prefix No More</b> 🚫\n"
                        f"━━━━━━━━━━━━━━━━━━\n\n"
                        f"Try another prefix to get more numbers."
                    ),
                    no_more_kb,
                )
            except Exception as e:
                print(f"[Prefix] No-more screen update failed: {e}")
            return

        phone_nums = [r[0] for r in rows]
        selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
        curr_time = int(time.time())

        # Prefix -> Change Number replaces the current Prefix lease for this
        # service. The old batch is retired only after a new matching batch
        # has been found above, so a failed search keeps the current numbers.
        await db.execute(
            "UPDATE numbers SET status = 'retired', assigned_to = NULL, assign_time = NULL "
            "WHERE assigned_to = ? AND service = ? AND status = 'busy'",
            (chat_id, service_name),
        )

        # Mark every newly delivered Prefix number as used immediately. This
        # is the same global history used by normal Change Number.
        for pn in phone_nums:
            await db.execute(
                "UPDATE numbers SET status = 'busy', assigned_to = ?, assign_time = ?, "
                "otp_received = 0, sms_seen_count = 0 "
                "WHERE phone_number = ? AND status = 'available' "
                "AND NOT EXISTS (SELECT 1 FROM number_usage_history h "
                "                WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''))",
                (chat_id, curr_time, pn),
            )
            await db.execute(
                "INSERT OR IGNORE INTO number_usage_history (phone_number, first_used_at) "
                "VALUES (?, ?)",
                (pn, curr_time),
            )

        await db.commit()

    # Start cooldown only after the new Prefix numbers were successfully allocated.
    PREFIX_CHANGE_LAST[uid] = time.monotonic()
    NUMBER_CHANGE_LAST[uid] = PREFIX_CHANGE_LAST[uid]

    # Do not use OFFSET here. The selected numbers are now removed from the
    # available pool, so the next click simply gets the next unused 3.
    state.prefix_filter_active[uid]["offset"] = int(active.get("offset", 0)) + len(phone_nums)

    display_country = country_name if country_name else (country_name_for_number(phone_nums[0]) if phone_nums else "Global")
    text_card, kb = build_hadi_number_card(
        service_name,
        country_name,
        phone_nums[:3],
        display_country,
        svc_tag(service_name),
        otp_rate=selected_rate,
        user_id=uid,
    )
    try:
        await safe_edit_by_ids(chat_id, message_id, text_card, kb)
    except Exception as e:
        print(f"[Prefix] Failed to load next 3 numbers: {e}")
    return


@dp.callback_query(F.data.startswith("hadi_chgctry_"))
async def cb_hadi_chgctry(c: types.CallbackQuery):
    try:
        await c.answer()
    except Exception:
        pass

    chat_id = c.message.chat.id
    service_name = c.data.split("_", 2)[2]

    # Country change clears any active Prefix filter.
    state.prefix_saved.pop(c.from_user.id, None)
    state.prefix_filter_active.pop(c.from_user.id, None)
    state.prefix_filter_pending.pop(c.from_user.id, None)

    # List countries that have unused, available stock for this service.
    # Show the country flag/name and its OTP rate, like the requested layout.
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """
            SELECT country, MIN(CAST(COALESCE(otp_rate, 0) AS REAL)) AS rate
            FROM numbers
            WHERE service = ? AND status = 'available'
              AND NOT EXISTS (
                SELECT 1 FROM number_usage_history h
                WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')
                    = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')
              )
            GROUP BY country
            ORDER BY country COLLATE NOCASE
            """,
            (service_name,)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} No countries available for this service right now.",
            {"inline_keyboard": [[btn("Back", callback_data="menu_stock_home", style="danger")]]}
        )
        return

    country_buttons = []
    for cname, rate in rows:
        cname = str(cname or "").strip()
        if not cname:
            continue
        cname_escaped = cname.replace(" ", "_")
        try:
            rate_text = f"{float(rate):.2f}Tk"
        except (TypeError, ValueError):
            rate_text = "Rate N/A"
        country_buttons.append({
            "text": f"{country_flag_emoji(cname)} {cname.upper()} · {rate_text.upper()}",
            "callback_data": f"hadi_ctry_{service_name}_{cname_escaped}"
        })

    # Two buttons per row. Pad the shorter label in each pair so the two
    # buttons occupy a visually balanced, near-full row beneath the heading.
    country_rows = []
    for i in range(0, len(country_buttons), 2):
        pair = country_buttons[i:i + 2]
        if len(pair) == 2:
            target_len = max(len(str(item.get("text", ""))) for item in pair)
            for item in pair:
                item["text"] = str(item.get("text", "")).ljust(target_len)
        country_rows.append(pair)
    country_rows.append([
        {"text": "BACK", "callback_data": "menu_stock_home"}
    ])

    # Service-specific heading + SELECT COUNTRY divider.
    service_title = html.escape(svc_display_name(service_name).upper())
    service_icon = svc_tag_icon(service_name)
    await safe_edit(
        c.message,
        f"{svc_tag_icon(service_name)} <b>{html.escape(svc_display_name(service_name).upper())}</b>    <b>SELECT COUNTRY</b> 👇👇",
        {"inline_keyboard": country_rows}
    )

# Per-user Change Number cooldown/lock.
# A user can change numbers only once every 5 seconds, and duplicate
# taps while a change is running are ignored safely.
NUMBER_CHANGE_LAST: dict[int, float] = {}
NUMBER_CHANGE_LOCKS: dict[int, asyncio.Lock] = {}


@dp.callback_query(F.data.startswith("hadi_chgnum_"))
async def cb_hadi_chgnum(c: types.CallbackQuery):
    chat_id = c.message.chat.id
    now = time.monotonic()
    last_change = NUMBER_CHANGE_LAST.get(chat_id, 0.0)
    remaining = 5.0 - (now - last_change)
    if remaining > 0:
        try:
            await c.answer(f"⏳ Please wait {remaining:.1f}s before changing again.", show_alert=True)
        except Exception:
            pass
        return

    lock = NUMBER_CHANGE_LOCKS.setdefault(chat_id, asyncio.Lock())
    if lock.locked():
        try:
            await c.answer("🔄 Number change is already in progress...", show_alert=True)
        except Exception:
            pass
        return

    async with lock:
        now = time.monotonic()
        last_change = NUMBER_CHANGE_LAST.get(chat_id, 0.0)
        remaining = 5.0 - (now - last_change)
        if remaining > 0:
            try:
                await c.answer(f"⏳ Please wait {remaining:.1f}s before changing again.", show_alert=True)
            except Exception:
                pass
            return

        # IMPORTANT: If the current card came from Prefix, the normal
        # Change Number button must NEVER fall back to random numbers.
        # Keep the exact same Prefix + country + service filter and allocate
        # only numbers matching the saved prefix.
        prefix_active = state.prefix_filter_active.get(chat_id)
        callback_service = str(c.data[len("hadi_chgnum_"):]).split("_", 1)[0].strip()
        if prefix_active:
            active_service = str(prefix_active.get("service") or "").strip()
            if active_service and (
                not callback_service
                or callback_service.lower() == active_service.lower()
            ):
                await _cb_hadi_prefix_more_impl(c)
                return

        # Show only one change-status message while the new numbers are loaded.
        # Do not show fresh/old/assigning details.
        try:
            await safe_edit(c.message, "🔄 <b>Changing Number</b>")
        except Exception:
            pass
        rest = c.data.split("_", 2)[2]
        svc_end = rest.index("_")
        service_name = rest[:svc_end]
        country_name = rest[svc_end + 1:].replace("_", " ")

        async with aiosqlite.connect(DB_FILE, timeout=2.0) as db:
            # Never let a temporary SQLite writer lock leave Telegram stuck on
            # “Changing Number”.  Wait briefly, then fail cleanly.
            await db.execute("PRAGMA busy_timeout = 1500")
            # Serialize replacement allocation to prevent concurrent reuse.
            try:
                await db.execute("BEGIN IMMEDIATE")
            except Exception as e:
                print(f"[Change Number] DB lock: {e}")
                try:
                    await db.rollback()
                except Exception:
                    pass
                await safe_edit(
                    c.message,
                    f"{ce(E_BROADCAST_FAIL, '⚠️')} <b>Number change is temporarily busy.</b>\n\nPlease try again in a moment.",
                    markup([
                        [btn("Change Number", E_TOOL_CHANGENUMBER, premium_emoji=True, callback_data=f"hadi_chgnum_{service_name}_{country_name.replace(' ', '_')}", style="danger")],
                        [btn("Change Country", suppress_auto_emoji=True, callback_data=f"hadi_chgctry_{service_name}", style="danger")],
                    ]),
                )
                return
            # Find replacements first; if none exist, keep the current lease.
            async with db.execute(
                "SELECT phone_number FROM numbers WHERE assigned_to = ? AND service = ? AND status = 'busy' ORDER BY rowid",
                (chat_id, service_name),
            ) as cur:
                old_rows = await cur.fetchall()
            old_phones = [r[0] for r in old_rows]

            if old_phones:
                placeholders = ",".join("?" for _ in old_phones)
                query = (
                    "SELECT phone_number, otp_rate FROM numbers "
                    "WHERE service = ? AND country = ? AND status = 'available' "
                    "AND NOT EXISTS (SELECT 1 FROM number_usage_history h WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')) "
                    f"AND phone_number NOT IN ({placeholders}) "
                    "ORDER BY RANDOM() LIMIT 3"
                )
                params = [service_name, country_name, *old_phones]
            else:
                query = (
                    "SELECT phone_number, otp_rate FROM numbers "
                    "WHERE service = ? AND country = ? AND status = 'available' "
                    "AND NOT EXISTS (SELECT 1 FROM number_usage_history h WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')) "
                    "ORDER BY RANDOM() LIMIT 3"
                )
                params = [service_name, country_name]

            try:
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
            except Exception as e:
                print(f"[Change Number] Query error: {e}")
                try:
                    await db.rollback()
                except Exception:
                    pass
                await safe_edit(
                    c.message,
                    f"{ce(E_BROADCAST_FAIL, '⚠️')} <b>Could not load a new number.</b>\n\nPlease try again.",
                    markup([
                        [btn("Change Number", E_TOOL_CHANGENUMBER, premium_emoji=True, callback_data=f"hadi_chgnum_{service_name}_{country_name.replace(' ', '_')}", style="danger")],
                        [btn("Change Country", suppress_auto_emoji=True, callback_data=f"hadi_chgctry_{service_name}", style="danger")],
                    ]),
                )
                return

            if not rows:
                await safe_edit(
                    c.message,
                    f"{ce(E_BROADCAST_FAIL, '❌')} <b>No different numbers available.</b>\n\nYour current number is still kept active.",
                    markup([
                        [btn("Change Number", E_TOOL_CHANGENUMBER, premium_emoji=True, callback_data=f"hadi_chgnum_{service_name}_{country_name.replace(' ', '_')}", style="danger")],
                        [btn("Change Country", suppress_auto_emoji=True, callback_data=f"hadi_chgctry_{service_name}", style="danger")],
                    ]),
                )
                return

            phone_nums = [r[0] for r in rows]
            selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
            curr_time = int(time.time())

            if old_phones:
                await db.execute(
                    "UPDATE numbers SET status = 'retired', assigned_to = NULL, assign_time = NULL WHERE assigned_to = ? AND service = ? AND status = 'busy'",
                    (chat_id, service_name),
                )

            for pn in phone_nums:
                await db.execute(
                    "UPDATE numbers SET status = 'busy', assigned_to = ?, assign_time = ?, otp_received = 0, sms_seen_count = 0 WHERE phone_number = ? AND status = 'available' AND NOT EXISTS (SELECT 1 FROM number_usage_history h WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''))",
                    (chat_id, curr_time, pn),
                )
                await db.execute(
                    "INSERT OR IGNORE INTO number_usage_history (phone_number, first_used_at) VALUES (?, ?)",
                    (pn, curr_time),
                )
            await db.commit()

        NUMBER_CHANGE_LAST[chat_id] = time.monotonic()

        display_country = get_country(phone_nums[0])
        label = svc_tag(service_name)
        text, kb = build_hadi_number_card(
            service_name, country_name, phone_nums, display_country, label, otp_rate=selected_rate, user_id=c.from_user.id
        )
        try:
            await safe_edit(c.message, text, kb)
        except Exception as e:
            if "message is not modified" in str(e).lower():
                await c.answer("⚠️ The number did not change. Please try again after 5 seconds.", show_alert=True)
            else:
                raise


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await safe_edit(c.message, await admin_stats_text(), admin_keyboard_for(c.from_user.id))

@dp.callback_query(F.data == "adm_cat_overview")
async def adm_cat_overview_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await safe_edit(c.message, await admin_stats_text(), admin_cat_overview_keyboard())

@dp.callback_query(F.data == "adm_cat_control")
async def adm_cat_control_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_WRENCH, '⚙️')} <b>Bot Control</b>",
        admin_cat_control_keyboard()
    )

@dp.callback_query(F.data == "adm_cat_users")
async def adm_cat_users_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_USERS, '👥')} <b>Users & Admins</b>",
        admin_cat_users_keyboard(c.from_user.id)
    )

@dp.callback_query(F.data == "adm_cat_earnings")
async def adm_cat_earnings_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_CASH, '💰')} <b>Earnings & Broadcast</b>",
        admin_cat_earnings_keyboard()
    )

@dp.callback_query(F.data == "adm_cat_stock")
async def adm_cat_stock_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await safe_edit(
        c.message,
        f"{ce(E_RK_GET_NUM, '📦')} <b>Stock Management</b>",
        admin_cat_stock_keyboard()
    )

# ── Backup users.json (users.json) ──
@dp.callback_query(F.data == "adm_backup_users")
async def adm_backup_users_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    if not os.path.exists(USERS_FILE):
        await c.message.answer(f"{ce(E_BROADCAST_FAIL, '❌')} <b>{USERS_FILE}</b> not found.", parse_mode="HTML")
        return

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"users_backup_{timestamp}.json"
        with open(USERS_FILE, "rb") as f:
            doc = types.BufferedInputFile(f.read(), filename=backup_name)
        await c.message.answer_document(
            doc,
            caption=(
                f"{ce(E_ADMIN_USERS, '👥')} <b>Users Database Backup</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{ce(E_ADMIN_DATE, '📅')} Date  : <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
                f"{ce(E_BROADCAST_USERS, '👥')} Users : <b>{len(users_db)}</b>"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        await c.message.answer(f"{ce(E_BROADCAST_FAIL, '❌')} Backup failed: <code>{html.escape(str(e))}</code>", parse_mode="HTML")

# ── Restore users.json / User Data ──
@dp.callback_query(F.data == "adm_restore_users")
async def adm_restore_users_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    state.admin_pending_actions[c.from_user.id] = "restore_users"
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_USERS, '👥')} <b>Upload User Data</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Send the previously downloaded <b>users.json</b> / user backup file.\n"
        f"The uploaded user records will be restored and saved as the current user database.",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
    )

# ── Export current positive user balances ──
@dp.callback_query(F.data == "adm_export_balances")
async def adm_export_balances_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    try:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                "SELECT user_id, balance FROM balances WHERE balance > 0 ORDER BY user_id"
            ) as cur:
                rows = await cur.fetchall()

        backup = {
            "format": "telegram_bot_user_balances_v1",
            "backup_id": secrets.token_hex(16),
            "exported_at": datetime.now(BD_TZ).isoformat(),
            "currency": "BDT",
            "balances": [
                {"user_id": int(user_id), "balance": round(float(balance or 0), 8)}
                for user_id, balance in rows
            ],
        }
        payload = json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8")
        filename = f"user_balances_{datetime.now(BD_TZ).strftime('%Y%m%d_%H%M%S')}.json"
        doc = types.BufferedInputFile(payload, filename=filename)
        total_amount = sum(float(row["balance"]) for row in backup["balances"])
        await c.message.answer_document(
            doc,
            caption=(
                "💰 <b>User Balance Backup</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👥 Users with positive balance: <b>{len(rows)}</b>\n"
                f"💵 Total balance: <b>{total_amount:.2f} TK</b>\n"
                "📌 Uploading this file credits these amounts to matching user IDs."
            ),
            parse_mode="HTML",
        )
    except Exception as err:
        await c.message.answer(
            f"❌ Balance export failed: <code>{html.escape(str(err))}</code>",
            parse_mode="HTML",
        )


# ── Import balances additively; each backup can be imported only once ──
@dp.callback_query(F.data == "adm_import_balances")
async def adm_import_balances_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    state.admin_pending_actions[c.from_user.id] = "import_user_balances"
    await safe_edit(
        c.message,
        "📤 <b>Upload User Balances</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "আগে Download Balances দিয়ে নেওয়া <b>JSON</b> ফাইলটি পাঠান।\n"
        "ফাইলের প্রতিটি User ID-এর টাকা তার বর্তমান ব্যালেন্সের সঙ্গে যোগ হবে।\n"
        "একই backup file দ্বিতীয়বার import করলে duplicate credit হবে না।",
        markup([btn("CANCEL", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")]),
    )


# ── Stats submenu ──
@dp.callback_query(F.data == "adm_stats")
async def adm_stats_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await safe_edit(c.message, await admin_stats_text(), admin_stats_keyboard())

# ── Co-Admin management (super admin only — co-admins cannot manage admins) ──
@dp.callback_query(F.data == "adm_co_admins")
async def adm_co_admins_cb(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        try: await c.answer("❌ Only the super admin can manage co-admins!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await safe_edit(c.message, admin_co_admins_text(), admin_co_admins_keyboard())

@dp.callback_query(F.data == "adm_addco")
async def adm_addco_cb(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        try: await c.answer("❌ Only the super admin can manage co-admins!", show_alert=True)
        except Exception: pass
        return
    if len(state.co_admin_ids) >= MAX_CO_ADMINS:
        await c.answer(f"⚠️ Max {MAX_CO_ADMINS} co-admins already added!", show_alert=True)
        return
    try: await c.answer()
    except Exception: pass
    state.admin_pending_actions[c.from_user.id] = "add_co_admin"
    await safe_edit(
        c.message,
        f"{ce(E_MENU_PIN, '📌')} <b>Send the Telegram User ID</b> of the person you want to add as co-admin.\n\n"
        f"<i>Ask them to send /start to @userinfobot (or similar) to get their numeric ID.</i>",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_co_admins", style="danger")])
    )

@dp.callback_query(F.data.startswith("adm_rmco_"))
async def adm_rmco_cb(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        try: await c.answer("❌ Only the super admin can manage co-admins!", show_alert=True)
        except Exception: pass
        return
    try:
        target = int(c.data[len("adm_rmco_"):])
    except ValueError:
        await c.answer("❌ Invalid ID!", show_alert=True)
        return
    state.co_admin_ids.discard(target)
    await save_co_admins(state.co_admin_ids)
    await c.answer(f"✅ Removed {target} as co-admin.", show_alert=True)
    await safe_edit(c.message, admin_co_admins_text(), admin_co_admins_keyboard())

# ── Broadcast submenu ──
@dp.callback_query(F.data == "adm_broadcast_menu")
async def adm_broadcast_menu_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    text = (
        f"{ce(E_BROADCAST, '📣')} <b>Broadcast</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_BROADCAST_USERS, '👥')} Total Users : <b>{len(state.known_users)}</b>\n"
        f"Press <b>Send Broadcast</b>\n"
        f"send any File Photo video"
    )
    await safe_edit(c.message, text, admin_broadcast_keyboard())

# ── Users submenu ──
@dp.callback_query(F.data == "adm_deleted_cleanup")
async def adm_deleted_cleanup_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer("🔍 Running safe cleanup...")
    except Exception: pass
    result = await _cleanup_deleted_accounts_once()
    await safe_edit(
        c.message,
        "🧹 <b>Deleted Account Cleanup</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔎 Checked: <b>{result['checked']}</b>\n"
        f"🟡 Quarantined: <b>{result['quarantined']}</b>\n"
        f"🗑️ Removed after 24h confirmation: <b>{result['removed']}</b>\n"
        f"⚠️ Skipped/invalid: <b>{result['skipped']}</b>\n\n"
        "<i>Safety mode: ambiguous Telegram/API errors never delete a user. "
        "Financial, OTP and referral history is never removed.</i>",
        markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
    )

# ── Users submenu ──
@dp.callback_query(F.data == "adm_users")
async def adm_users_cb(c: types.CallbackQuery):
    _stop_all_users_refresh(c.from_user.id)
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    # Keep all three counters based on the same registered-user database.
    # This prevents stale IDs in the in-memory sets from making Verified or
    # Banned larger than Total Registered.
    registered_ids = set()
    for uid_str in users_db.keys():
        try:
            registered_ids.add(int(uid_str))
        except (TypeError, ValueError):
            continue
    state.known_users.intersection_update(registered_ids)
    state.verified_users.intersection_update(registered_ids)
    state.banned_users.intersection_update(registered_ids)

    text = (
        f"{ce(E_ADMIN_USERS, '👥')} <b>User Management</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  • Total Registered : <b>{len(registered_ids)}</b>\n"
        f"  • Verified         : <b>{len(state.verified_users)}</b>\n"
        f"  • Banned           : <b>{len(state.banned_users)}</b>"
    )
    await safe_edit(c.message, text, admin_users_keyboard())

async def _render_all_user_otp_stats(message, page: int = 0):
    """Render a compact, clickable user list with each user's referral count."""
    per_page = 10
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """SELECT referrer_id, COUNT(*) FROM referrals
               WHERE referrer_id IS NOT NULL GROUP BY referrer_id"""
        ) as cur:
            referral_counts = {int(uid): int(count or 0) for uid, count in await cur.fetchall()}

    # Show ONLY users who have successfully referred at least one person.
    # Ordinary users and users with zero referrals must not appear here.
    referrer_ids = {uid for uid, count in referral_counts.items() if count > 0}
    ordered_ids = sorted(
        referrer_ids,
        key=lambda uid: (referral_counts.get(uid, 0), -uid),
        reverse=True,
    )
    total = len(ordered_ids)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    page_ids = ordered_ids[page * per_page:(page + 1) * per_page]

    lines = [
        "👥 <b>Rafer Details</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    rows = []
    if not page_ids:
        lines.append("এখনো কেউ রেফার করেনি।")
    for uid in page_ids:
        info = users_db.get(str(uid), {}) if isinstance(users_db, dict) else {}
        name = " ".join(filter(None, [
            str(info.get("first_name", "")).strip(),
            str(info.get("last_name", "")).strip(),
        ])) or str(info.get("username", "")).strip() or f"User {uid}"
        rows.append([btn(f"👤 {name[:35]} · Refer: {referral_counts.get(uid, 0)}", E_ADMIN_USERS, callback_data=f"adm_user_stats_detail_{uid}_0", style="danger")])

    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", E_TOOL_BACKBUTTON, callback_data=f"adm_user_stats_page_{page - 1}", style="danger"))
    nav.append(btn(f"📄 {page + 1}/{total_pages}", E_TOOL_REFRESHING, callback_data="noop", style="danger"))
    if page + 1 < total_pages:
        nav.append(btn("Next ➡️", E_TOOL_REFRESHING, callback_data=f"adm_user_stats_page_{page + 1}", style="danger"))
    rows.append(nav)
    rows.append([btn("🔄 Refresh", E_TOOL_REFRESHING, callback_data=f"adm_user_stats_page_{page}", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
    await safe_edit(message, "\n".join(lines), {"inline_keyboard": rows})

@dp.callback_query(F.data == "adm_user_stats")
async def adm_user_stats_cb(c: types.CallbackQuery):
    """Admin-only report listing all users with daily and lifetime OTP totals."""
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await _render_all_user_otp_stats(c.message, 0)


@dp.callback_query(F.data.startswith("adm_user_stats_page_"))
async def adm_user_stats_page_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try:
        page = int(c.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        page = 0
    try: await c.answer()
    except Exception: pass
    await _render_all_user_otp_stats(c.message, page)

@dp.callback_query(F.data.startswith("adm_user_stats_detail_"))
async def adm_user_stats_detail_cb(c: types.CallbackQuery):
    """Show a referrer's profile and daily/lifetime OTP for every referred user."""
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try:
        _, _, _, _, uid_text, page_text = c.data.split("_", 5)
        referrer_id, page = int(uid_text), max(0, int(page_text))
    except (TypeError, ValueError):
        try: await c.answer("Invalid user", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    today = bd_today()
    async with aiosqlite.connect(DB_FILE) as db:
        # Show referred members from highest current balance to lowest.
        # Members with equal balances keep a stable recent-join-first order.
        async with db.execute(
            """SELECT r.user_id
               FROM referrals AS r
               LEFT JOIN balances AS b ON b.user_id = r.user_id
               WHERE r.referrer_id = ?
               ORDER BY COALESCE(b.balance, 0) DESC, r.joined_at DESC, r.user_id ASC""",
            (referrer_id,),
        ) as cur:
            referred_ids = [int(row[0]) for row in await cur.fetchall()]
        per_page = 10
        total_pages = max(1, (len(referred_ids) + per_page - 1) // per_page)
        page = min(page, total_pages - 1)
        page_ids = referred_ids[page * per_page:(page + 1) * per_page]
        daily_counts, lifetime_counts = {}, {}
        if page_ids:
            placeholders = ",".join("?" for _ in page_ids)
            async with db.execute(f"SELECT user_id, SUM(otp_count) FROM user_daily_otp_stats WHERE otp_date = ? AND user_id IN ({placeholders}) GROUP BY user_id", (today, *page_ids)) as cur:
                daily_counts = {int(uid): int(count or 0) for uid, count in await cur.fetchall()}
            async with db.execute(f"SELECT user_id, SUM(otp_count) FROM user_otp_stats WHERE user_id IN ({placeholders}) GROUP BY user_id", tuple(page_ids)) as cur:
                lifetime_counts = {int(uid): int(count or 0) for uid, count in await cur.fetchall()}

    info = users_db.get(str(referrer_id), {}) if isinstance(users_db, dict) else {}
    ref_username = str(info.get("username", "")).strip().lstrip("@")
    ref_name = " ".join(filter(None, [str(info.get("first_name", "")).strip(), str(info.get("last_name", "")).strip()])) or (f"@{ref_username}" if ref_username else "Unknown")
    # Keep the message body clean: page numbers belong only in the inline
    # navigation row beside Prev/Next, not in the text above.
    lines = [
        f"👤 <b>{html.escape(ref_name)}</b>",
        f"🆔 <code>{referrer_id}</code>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not page_ids:
        lines.append("এই ইউজার এখনো কাউকে Refer করেনি।")
    else:
        lines.append("")
    for index, uid in enumerate(page_ids):
        child = users_db.get(str(uid), {}) if isinstance(users_db, dict) else {}
        child_name = " ".join(filter(None, [
            str(child.get("first_name", "")).strip(),
            str(child.get("last_name", "")).strip(),
        ])) or str(child.get("username", "")).strip() or "Unknown"
        if index:
            lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"👤 <b>{html.escape(child_name)}</b>\n"
            f"🆔 <code>{uid}</code>\n"
            f"📅 Today OTP: <b>{daily_counts.get(uid, 0)}</b>\n"
            f"🏆 Lifetime OTP: <b>{lifetime_counts.get(uid, 0)}</b>"
        )

    rows = []
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", E_TOOL_BACKBUTTON, callback_data=f"adm_user_stats_detail_{referrer_id}_{page - 1}", style="danger"))
    nav.append(btn(f"📄 {page + 1}/{total_pages}", E_TOOL_REFRESHING, callback_data="noop", style="danger"))
    if page + 1 < total_pages:
        nav.append(btn("Next ➡️", E_TOOL_REFRESHING, callback_data=f"adm_user_stats_detail_{referrer_id}_{page + 1}", style="danger"))
    rows.append(nav)
    rows.append([btn("🔄 Refresh", E_TOOL_REFRESHING, callback_data=f"adm_user_stats_detail_{referrer_id}_{page}", style="danger")])
    rows.append([btn("↩️ Back to All Users", E_TOOL_BACKBUTTON, callback_data="adm_user_stats_page_0", style="danger")])
    await safe_edit(c.message, "\n".join(lines), {"inline_keyboard": rows})


# ── Add / Deduct Balance ──
@dp.callback_query(F.data == "adm_add_balance")
async def adm_add_balance_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    state.admin_pending_actions[c.from_user.id] = "add_balance_uid"
    state.admin_temp_data.pop(c.from_user.id, None)
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_CASH, '💰')} <b>Add / Deduct Balance</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} Send the <b>Telegram User ID</b> of the user.",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
    )

@dp.callback_query(F.data.startswith("adm_users_list_"))
async def adm_users_list_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    page = int(c.data.split("_")[-1])
    per_page = 10
    all_users = list(users_db.items())
    total = len(all_users)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    chunk = all_users[page*per_page:(page+1)*per_page]
    lines = []
    for uid_str, info in chunk:
        name = (info.get("first_name","") + " " + info.get("last_name","")).strip()
        uname = info.get("username","")
        banned = f" {ce(E_BROADCAST_FAIL, '🚫')}" if int(uid_str) in state.banned_users else ""
        lines.append(f"<code>{uid_str}</code> — {name} {uname}{banned}")
    text = (
        f"{ce(E_ADMIN_USERS, '??')} <b>User List</b>"
        f" — Total: <b>{total}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lines)
    )
    nav_row = []
    if page > 0:
        nav_row.append(btn("Prev", E_TOOL_BACKBUTTON, callback_data=f"adm_users_list_{page-1}", style="danger"))
    nav_row.append(btn(f"📄 {page + 1}/{total_pages}", E_TOOL_REFRESHING, callback_data="noop", style="danger"))
    if page + 1 < total_pages:
        nav_row.append(btn("Next", E_TOOL_REFRESHING, callback_data=f"adm_users_list_{page+1}", style="danger"))
    rows = [nav_row]
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
    await safe_edit(c.message, text, {"inline_keyboard": rows})

@dp.callback_query(F.data == "adm_user_ban")
async def adm_user_ban_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    state.pending_user_search[c.from_user.id] = 'ban'
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_WRENCH, '🔧')} <b>Ban User</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Send the <b>User ID</b> to ban.\n"
        f"Example: <code>123456789</code>",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
    )

@dp.callback_query(F.data == "adm_user_unban")
async def adm_user_unban_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    if not state.banned_users:
        await c.answer("No banned users!", show_alert=True)
        return
    state.pending_user_search[c.from_user.id] = 'unban'
    banned_list = ", ".join(f"<code>{u}</code>" for u in list(state.banned_users)[:20])
    await safe_edit(
        c.message,
        f"{ce(E_FWD_OK, '✅')} <b>Unban User</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Banned users: {banned_list}\n\n"
        f"Send the <b>User ID</b> to unban.",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
    )


# ── New compact User Management ─────────────────────────────────────────────
async def _user_total_otp(user_id: int) -> int:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(otp_count), 0) FROM user_otp_stats WHERE user_id = ?",
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] or 0) if row else 0

async def _worked_user_rows(limit: int | None = None, offset: int = 0):
    """Return users who have OTP history, with balance.

    Kept for the existing Top 10 / detail features.  All Users uses
    _all_user_rows() below so users with zero OTP are also included.
    """
    sql = (
        "SELECT s.user_id, COALESCE(SUM(s.otp_count), 0) AS total_otp, "
        "COALESCE(b.balance, 0) AS balance "
        "FROM user_otp_stats s LEFT JOIN balances b ON b.user_id = s.user_id "
        "GROUP BY s.user_id ORDER BY total_otp DESC, s.user_id ASC"
    )
    params = []
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [int(limit), int(offset)]
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(sql, params) as cur:
            return await cur.fetchall()


async def _all_user_rows():
    """Return every known user and whether they currently have a busy number.

    Working = at least one row in numbers with status='busy' and assigned_to
    equal to the user's Telegram ID.  This is intentionally independent of
    OTP history, so users who are currently working but have received no OTP
    yet are still shown as Working.
    """
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """SELECT u.user_id,
                      COALESCE(o.total_otp, 0) AS total_otp,
                      COALESCE(b.balance, 0) AS balance,
                      COALESCE(n.busy_count, 0) AS busy_count
               FROM (
                    SELECT user_id FROM user_otp_stats
                    UNION
                    SELECT user_id FROM balances
                    UNION
                    SELECT DISTINCT assigned_to AS user_id
                      FROM numbers
                     WHERE assigned_to IS NOT NULL AND assigned_to != ''
               ) u
               LEFT JOIN (
                    SELECT user_id, SUM(otp_count) AS total_otp
                      FROM user_otp_stats
                     GROUP BY user_id
               ) o ON o.user_id = u.user_id
               LEFT JOIN balances b ON b.user_id = u.user_id
               LEFT JOIN (
                    SELECT CAST(assigned_to AS INTEGER) AS user_id, COUNT(*) AS busy_count
                      FROM numbers
                     WHERE status = 'busy'
                       AND assigned_to IS NOT NULL
                       AND assigned_to != ''
                     GROUP BY CAST(assigned_to AS INTEGER)
               ) n ON n.user_id = CAST(u.user_id AS INTEGER)
              ORDER BY COALESCE(b.balance, 0) DESC,
                       CASE WHEN COALESCE(n.busy_count, 0) > 0 THEN 0 ELSE 1 END,
                       CAST(u.user_id AS INTEGER) ASC"""
        ) as cur:
            rows = await cur.fetchall()

    # Include legacy/in-memory users_db users that may not yet have a DB row.
    known = {int(r[0]) for r in rows if r[0] is not None}
    if isinstance(users_db, dict):
        for key in users_db.keys():
            try:
                uid = int(key)
            except (TypeError, ValueError):
                continue
            if uid not in known:
                rows.append((uid, 0, 0.0, 0))

    # Show users in descending balance order: highest balance first.
    # For equal balances, active users appear first, then lower user IDs.
    rows = sorted(
        rows,
        key=lambda r: (-float(r[2] or 0), 0 if int(r[3] or 0) > 0 else 1, int(r[0]))
    )

    # Add the original registration/join timestamp from users.json without
    # changing the existing database/user ordering or activity logic.
    rows = [
        (*r, str((users_db.get(str(int(r[0])), {}) if isinstance(users_db, dict) else {}).get("joined_at", "") or ""))
        for r in rows
    ]
    return rows


def _user_display_name(user_id: int) -> str:
    """Return the best locally-known Telegram display name.

    Prefer first/last name, then username.  A stable User ID label is used
    only when the legacy local record has no profile fields.
    """
    info = users_db.get(str(int(user_id)), {}) if isinstance(users_db, dict) else {}
    name = " ".join(filter(None, [
        str(info.get("first_name", "")).strip(),
        str(info.get("last_name", "")).strip(),
    ])).strip()
    if name:
        return name
    username = str(info.get("username", "")).strip()
    if username:
        return username if username.startswith("@") else f"@{username}"
    return f"User {int(user_id)}"


async def _ensure_user_profile(user_id: int) -> str:
    """Refresh missing legacy profile fields from Telegram when possible."""
    uid = int(user_id)
    key = str(uid)
    info = users_db.setdefault(key, {"chat_id": uid})
    local_name = " ".join(filter(None, [
        str(info.get("first_name", "")).strip(),
        str(info.get("last_name", "")).strip(),
    ])).strip()
    local_username = str(info.get("username", "")).strip()
    if local_name or local_username:
        return _user_display_name(uid)

    try:
        chat = await bot.get_chat(uid)
        first_name = str(getattr(chat, "first_name", "") or "").strip()
        last_name = str(getattr(chat, "last_name", "") or "").strip()
        username = str(getattr(chat, "username", "") or "").strip()
        if first_name or last_name or username:
            info["chat_id"] = uid
            info["first_name"] = first_name
            info["last_name"] = last_name
            info["username"] = f"@{username}" if username else ""
            info.setdefault("joined_at", datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S"))
            info["last_seen"] = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
            save_users(users_db)
    except Exception as e:
        print(f"[Users] Profile refresh failed for {uid}: {e}")

    return _user_display_name(uid)


def _user_detail_text(user_id: int, total_otp: int, balance: float) -> str:
    name = html.escape(_user_display_name(user_id))
    blocked = int(user_id) in state.banned_users
    status = "🔴 Blocked" if blocked else "🟢 Active"
    return (
        f"👤 <b>Name:</b> {name}\n"
        f"🆔 <b>User ID:</b> <code>{int(user_id)}</code>\n"
        f"🔢 <b>Total OTP:</b> <b>{int(total_otp)}</b>\n"
        f"💰 <b>Total Balance:</b> <b>{float(balance):.2f} TK</b>\n"
        f"📌 <b>Status:</b> {status}"
    )


_all_users_refresh_tasks = {}


def _stop_all_users_refresh(admin_id: int):
    task = _all_users_refresh_tasks.pop(int(admin_id), None)
    if task and not task.done():
        task.cancel()


async def _render_all_users_once(message, page: int = 0):
    per_page = 10
    all_rows = await _all_user_rows()
    total = len(all_rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    rows = all_rows[page * per_page:(page + 1) * per_page]
    working_count = sum(1 for r in all_rows if int(r[3] or 0) > 0)
    not_working_count = total - working_count

    lines = [
        f"{ce(E_ADMIN_USERS, '👥')} <b>ALL USERS</b>",
        "━━━━━━━━━━━━━━━━━",
        f"📊 Total: {total}",
        f"🟢 Active : {working_count}",
        f"🔴 Inactive : {not_working_count}",
        "",
    ]
    buttons = []
    if not rows:
        lines.append("<i>No users found.</i>")
    else:
        for uid, total_otp, balance, busy_count, joined_at in rows:
            uid_int = int(uid)
            await _ensure_user_profile(uid_int)
            working = int(busy_count or 0) > 0
            name = html.escape(_user_display_name(uid_int))
            join_display = html.escape(str(joined_at or "Not available"))
            lines.extend([
                f"👤 {name}",
                f"🆔 {uid_int}",
                f"📅 Joined: {join_display}",
                f"💬 OTP: {int(total_otp or 0)}  •  💰 {float(balance or 0):.2f} TK",
                "",
            ])
    nav = []
    if page > 0:
        nav.append(btn("Prev", E_TOOL_BACKBUTTON, callback_data=f"adm_all_users_page_{page-1}", style="danger"))
    nav.append(btn(f"📄 {page + 1}/{total_pages}", E_TOOL_REFRESHING, callback_data="noop", style="danger"))
    if page + 1 < total_pages:
        nav.append(btn("Next", E_TOOL_REFRESHING, callback_data=f"adm_all_users_page_{page+1}", style="danger"))
    buttons.append(nav)
    buttons.append([btn("🔎 Search User ID", E_ADMIN_WRENCH, callback_data="adm_all_user_search", style="danger")])
    buttons.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
    await safe_edit(message, "\n".join(lines).rstrip(), {"inline_keyboard": buttons})


async def _all_users_live_loop(admin_id: int, chat_id: int, message_id: int, page: int):
    try:
        while True:
            await asyncio.sleep(1)
            current_task = _all_users_refresh_tasks.get(int(admin_id))
            if current_task is not asyncio.current_task():
                return
            message = types.SimpleNamespace(chat=types.SimpleNamespace(id=chat_id), message_id=message_id)
            await _render_all_users_once(message, page)
    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"[AllUsers] Live refresh stopped: {e}")


async def _render_all_users(message, page: int = 0, start_live: bool = True, admin_id: int = 0):
    if start_live and admin_id:
        _stop_all_users_refresh(admin_id)
    await _render_all_users_once(message, page)
    if start_live and admin_id:
        task = asyncio.create_task(
            _all_users_live_loop(admin_id, message.chat.id, message.message_id, int(page))
        )
        _all_users_refresh_tasks[int(admin_id)] = task


@dp.callback_query(F.data == "adm_all_users")
async def adm_all_users_new_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await _render_all_users(c.message, 0, start_live=True, admin_id=int(c.from_user.id))


@dp.callback_query(F.data.regexp(r"^adm_all_users_page_\d+$"))
async def adm_all_users_page_new_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    page = int(c.data.rsplit("_", 1)[1])
    await _render_all_users(c.message, page, start_live=True, admin_id=int(c.from_user.id))


@dp.callback_query(F.data == "adm_all_user_search")
async def adm_all_user_search_cb(c: types.CallbackQuery):
    _stop_all_users_refresh(c.from_user.id)
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    uid = c.from_user.id
    await _delete_admin_flow_message(uid, "status_id")
    state.admin_pending_actions[uid] = "all_user_search"
    await safe_edit(
        c.message,
        f"🔎 <b>Search User</b>\n━━━━━━━━━━━━━━━━━━━━\n\nSend the <b>User ID</b>.",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_all_users", style="danger")]),
    )
    await _track_admin_flow_message(uid, "prompt_id", c.message)


@dp.callback_query(F.data.regexp(r"^adm_user_detail_\d+$"))
async def adm_user_detail_new_cb(c: types.CallbackQuery):
    _stop_all_users_refresh(c.from_user.id)
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    uid = int(c.data.rsplit("_", 1)[1])
    try: await c.answer()
    except Exception: pass
    total_otp = await _user_total_otp(uid)
    balance = await get_balance(uid)
    blocked = uid in state.banned_users
    toggle_text = "UNBLOCK USER" if blocked else "BLOCK USER"
    toggle_cb = f"adm_user_toggle_block_{uid}"
    await safe_edit(
        c.message,
        _user_detail_text(uid, total_otp, balance),
        markup(
            [btn(toggle_text, E_FWD_OK if blocked else E_BROADCAST_FAIL, callback_data=toggle_cb, style="__status_red__" if blocked else None)],
            [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_all_users", style="danger")],
        ),
    )


@dp.callback_query(F.data.regexp(r"^adm_user_toggle_block_\d+$"))
async def adm_user_toggle_block_new_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    uid = int(c.data.rsplit("_", 1)[1])
    if uid in state.banned_users:
        state.banned_users.discard(uid)
        msg = "User unblocked"
    else:
        state.banned_users.add(uid)
        state.verified_users.discard(uid)
        msg = "User blocked"
    try: await c.answer(msg, show_alert=True)
    except Exception: pass
    total_otp = await _user_total_otp(uid)
    balance = await get_balance(uid)
    blocked = uid in state.banned_users
    await safe_edit(
        c.message,
        _user_detail_text(uid, total_otp, balance),
        markup(
            [btn("UNBLOCK USER" if blocked else "BLOCK USER", E_FWD_OK if blocked else E_BROADCAST_FAIL,
                 callback_data=f"adm_user_toggle_block_{uid}", style="__status_red__" if blocked else None)],
            [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_all_users", style="danger")],
        ),
    )


@dp.callback_query(F.data == "adm_top10_users")
async def adm_top10_users_new_cb(c: types.CallbackQuery):
    """Show the top 10 users ranked ONLY by OTP received today (Bangladesh date)."""
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    today = bd_today()
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """SELECT d.user_id, COALESCE(d.otp_count, 0) AS today_otp,
                      COALESCE(b.balance, 0) AS balance
               FROM user_daily_otp_stats d
               LEFT JOIN balances b ON b.user_id = d.user_id
               WHERE d.otp_date = ? AND COALESCE(d.otp_count, 0) > 0
               ORDER BY today_otp DESC, d.user_id ASC
               LIMIT 10""",
            (today,),
        ) as cur:
            rows = await cur.fetchall()

    lines = ["🗓️ <b>Daily Leaderboard</b>", "━━━━━━━━━━━━━━━━━━━━", "", f"📅 Date: {today}", ""]
    if not rows:
        lines.append("আজকে এখনো কোনো OTP রেকর্ড হয়নি।")
    else:
        for rank, (uid, today_otp, balance) in enumerate(rows, 1):
            name = html.escape(_user_display_name(int(uid)))
            lines.append(
                f"<b>{rank} {name}</b>\n"
                f"🆔 User ID: <code>{int(uid)}</code>\n"
                f"🔢 Today OTP: <b>{int(today_otp or 0)}</b>\n"
                f"💰 TOTAL BALANCE: <b>{float(balance or 0):.2f} TK</b>\n"
            )
    await safe_edit(c.message, "\n".join(lines), markup(
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")]
    ))


@dp.callback_query(F.data == "adm_remove_balance")
async def adm_remove_balance_new_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    state.admin_pending_actions[c.from_user.id] = "remove_balance_uid"
    state.admin_temp_data.pop(c.from_user.id, None)
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_CASH, '💰')} <b>Remove Balance</b>\n━━━━━━━━━━━━━━━━━━━━\n\nSend the <b>Telegram User ID</b>.",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")]),
    )

# ── Bot Settings submenu ──
@dp.callback_query(F.data == "adm_country_rates")
async def adm_country_rates_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    await c.answer()
    rows = [[btn("🗺️ Global Country Rates", E_MENU_GLOBE2, callback_data="adm_cr_global", style="danger")]]
    services = list(RATE_SERVICES)
    try:
        services += await get_custom_rate_services()
    except Exception:
        pass
    seen = set()
    for svc in services:
        key = _rate_key(svc)
        if key in seen:
            continue
        seen.add(key)
        rows.append([btn(f"🗺️ {svc_display_name(svc)} — Country Rates", E_MENU_GLOBE2, callback_data=f"adm_cr_svc|{svc}", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
    await safe_edit(c.message, f"🗺️ <b>Country Rates</b>\n\nChoose <b>Global</b> to use one country rate for all services, or choose a service to set a service-specific country rate.\n\n<b>Priority:</b> Service + Country → Global Country → Service Rate", {"inline_keyboard": rows})

async def _available_countries_for_service(svc=None):
    async with aiosqlite.connect(DB_FILE) as db:
        if svc is None:
            async with db.execute("SELECT DISTINCT country FROM numbers WHERE country IS NOT NULL AND TRIM(country) != '' ORDER BY country") as cur:
                return [r[0] for r in await cur.fetchall() if r and r[0]]
        async with db.execute("SELECT DISTINCT country FROM numbers WHERE service = ? AND country IS NOT NULL AND TRIM(country) != '' ORDER BY country", (svc,)) as cur:
            return [r[0] for r in await cur.fetchall() if r and r[0]]

@dp.callback_query(F.data == "adm_cr_global")
async def adm_global_country_rates_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    await c.answer()
    countries = await _available_countries_for_service()
    if not countries:
        await safe_edit(c.message, "❌ No countries found in stock yet.", markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_country_rates", style="danger")]))
        return
    rows = []
    for country in countries:
        rate = get_global_country_rate(country)
        shown = rate if rate is not None else 0.0
        tag = "override" if rate is not None else "not set"
        rows.append([btn(f"{country} — {format_dual_rate(shown)} ({tag})", E_MENU_GLOBE2, callback_data=f"adm_cr_global_set|{country}", style="danger" if rate is not None and shown > 0 else "danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_country_rates", style="danger")])
    await safe_edit(c.message, "🗺️ <b>Global Country Rates</b>\n\nThis rate applies to every service unless a service-specific country rate is set.", {"inline_keyboard": rows})

@dp.callback_query(F.data.startswith("adm_cr_global_set|"))
async def adm_global_country_rate_set_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    await c.answer()
    country = c.data.split("|", 1)[1]
    current = get_global_country_rate(country)
    current_text = format_dual_rate(current) if current is not None else "Not set"
    state.admin_pending_actions[c.from_user.id] = f"set_global_country_rate|{country}"
    await safe_edit(c.message, f"🗺️ <b>Global Country Rate — {country}</b>\n\nCurrent: <b>{current_text}</b>\n\nনতুন রেট টাকা হিসেবে পাঠান (যেমন <code>1.50</code>)। বন্ধ করতে <code>0</code> পাঠান।", markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_cr_global", style="danger")]))

@dp.callback_query(F.data.startswith("adm_cr_svc|"))
async def adm_country_rate_service_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    await c.answer()
    svc = c.data.split("|", 1)[1]
    countries = await _available_countries_for_service(svc)
    if not countries:
        await safe_edit(c.message, f"❌ No countries found for <b>{svc_display_name(svc)}</b>.", markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_country_rates", style="danger")]))
        return
    rows = []
    for country in countries:
        rate = get_country_rate(svc, country)
        global_rate = get_global_country_rate(country)
        fallback = global_rate if global_rate is not None else get_service_rate(svc)
        shown = rate if rate is not None else fallback
        tag = "service override" if rate is not None else ("global" if global_rate is not None else "service default")
        rows.append([btn(f"{country} — {format_dual_rate(shown)} ({tag})", E_MENU_GLOBE2, callback_data=f"adm_cr_set|{svc}|{country}", style="danger" if shown > 0 else "danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_country_rates", style="danger")])
    await safe_edit(c.message, f"🗺️ <b>{svc_display_name(svc)} Country Rates</b>\n\nSet a service-specific rate. If you do not set one, the Global Country Rate (if any) is used.", {"inline_keyboard": rows})

@dp.callback_query(F.data.startswith("adm_cr_set|"))
async def adm_country_rate_set_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    await c.answer()
    parts = c.data.split("|", 2)
    if len(parts) != 3:
        return
    svc, country = parts[1], parts[2]
    current = get_country_rate(svc, country)
    global_rate = get_global_country_rate(country)
    if current is not None:
        current_text = f"{format_dual_rate(current)} (service override)"
    elif global_rate is not None:
        current_text = f"{format_dual_rate(global_rate)} (global)"
    else:
        current_text = f"{format_dual_rate(get_service_rate(svc))} (service default)"
    state.admin_pending_actions[c.from_user.id] = f"set_country_rate|{svc}|{country}"
    await safe_edit(c.message, f"🗺️ <b>{svc_display_name(svc)} — {country}</b>\n\nCurrent: <b>{current_text}</b>\n\nনতুন সার্ভিস রেট টাকা হিসেবে পাঠান (যেমন <code>1.50</code>)। বন্ধ করে Global Country Rate ব্যবহার করতে <code>0</code> পাঠান।", markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data=f"adm_cr_svc|{svc}", style="danger")]))


@dp.callback_query(F.data == "adm_referral_settings")
async def adm_referral_settings_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    text = (
        "🔗 <b>Referral Commission & Bonus</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📈 Lifetime commission: <b>{get_referral_commission_rate() * 100:.2f}%</b>\n"
        f"🎁 One-time new referral bonus: <b>{get_referral_bonus_per_refer():.2f} TK</b>\n\n"
        "কমিশন পরিবর্তন করলে নতুন earning থেকে নতুন rate কার্যকর হবে।\n"
        "বোনাস পরিবর্তন করলে পরবর্তী সফল নতুন referral-এ নতুন amount কার্যকর হবে।"
    )
    await safe_edit(c.message, text, markup(
        [btn("✏️ Set Lifetime Commission (%)", E_RK_REFER_EARN, callback_data="adm_referral_set_commission", style="danger")],
        [btn("✏️ Set Referral Bonus (TK)", E_ADMIN_CASH, callback_data="adm_referral_set_bonus", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")],
    ))


@dp.callback_query(F.data.in_({"adm_referral_set_commission", "adm_referral_set_bonus"}))
async def adm_referral_setting_prompt_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    if c.data == "adm_referral_set_commission":
        state.admin_pending_actions[c.from_user.id] = "set_referral_commission"
        prompt = (
            "📈 <b>Lifetime Referral Commission</b>\n\n"
            f"Current: <b>{get_referral_commission_rate() * 100:.2f}%</b>\n"
            "নতুন কমিশন শতাংশ পাঠান (যেমন <code>5</code> মানে 5%, <code>2.5</code> মানে 2.5%)।\n"
            "সীমা: 0 থেকে 100।"
        )
    else:
        state.admin_pending_actions[c.from_user.id] = "set_referral_bonus"
        prompt = (
            "🎁 <b>Referral Bonus</b>\n\n"
            f"Current: <b>{get_referral_bonus_per_refer():.2f} TK</b>\n"
            "প্রতি সফল নতুন referral-এর জন্য নতুন বোনাসের টাকা পাঠান (যেমন <code>1</code> বা <code>2.50</code>)।\n"
            "সীমা: 0 বা তার বেশি।"
        )
    await safe_edit(c.message, prompt, markup([btn("↩️ Cancel", E_TOOL_BACKBUTTON, callback_data="adm_referral_settings", style="danger")]))


@dp.callback_query(F.data.in_({"adm_set_min_withdraw", "adm_min_withdraw_direct"}))
async def adm_set_min_withdraw_prompt_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    current = get_min_withdraw()
    direct_from_admin_panel = c.data == "adm_min_withdraw_direct"
    state.admin_pending_actions[c.from_user.id] = (
        "set_min_withdraw_direct" if direct_from_admin_panel else "set_min_withdraw"
    )
    back_callback = "admin_panel" if direct_from_admin_panel else "adm_rates"

    await safe_edit(
        c.message,
        f"{ce(E_OTP_KEY, '✏️')} <b>Minimum Withdrawal Amount</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current minimum: <b>{current:.2f} TK</b>\n\n"
        f"{ce(E_MENU_PIN, '📌')} Send the new minimum withdrawal amount in TK (e.g. <code>10</code>).",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data=back_callback, style="danger")])
    )

@dp.callback_query(F.data == "adm_toggle_withdraw")
async def adm_toggle_withdraw_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try:
            await c.answer("❌ Access Denied!", show_alert=True)
        except Exception:
            pass
        return

    new_state = not is_withdraw_enabled()
    await set_withdraw_enabled(new_state)
    try:
        await c.answer(
            f"Withdrawal {'enabled' if new_state else 'disabled'} successfully!",
            show_alert=True,
        )
    except Exception:
        pass

    # Return to Admin Settings after toggling withdrawal.
    # Do not show the Rate/service buttons here.
    await safe_edit(c.message, "⚙️ <b>Admin Settings</b>", admin_settings_keyboard())


@dp.callback_query(F.data == "adm_usd_exchange")
async def adm_usd_exchange_prompt_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    state.admin_pending_actions[c.from_user.id] = "set_usd_exchange_rate"
    await safe_edit(
        c.message,
        "⚙️ <b>Profile & Finance USD Rate</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"বর্তমান হিসাবের রেট: <b>৳{get_usd_to_bdt_rate():g}</b>\n\n"
        "প্রোফাইলের ডলার সমমান এবং Binance withdrawal হিসাবের জন্য নতুন রেট পাঠান। উদাহরণ: <code>125</code>।",
        markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
    )


@dp.callback_query(F.data.startswith("adm_set_rate_"))
async def adm_set_rate_prompt_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    svc = c.data[len("adm_set_rate_"):].strip()
    if not svc or svc not in RATE_SERVICES:
        await safe_edit(c.message, "❌ Invalid service. Please reopen Rate.", await admin_rates_keyboard())
        return
    current = get_service_rate(svc)
    state.admin_pending_actions[c.from_user.id] = f"set_rate_{svc}"

    await safe_edit(
        c.message,
        f"{ce(E_OTP_KEY, '✏️')} <b>{svc_display_name(svc)} Rate</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current rate: <b>{format_dual_rate(current)}</b> / successful OTP\n\n"
        f"{ce(E_MENU_PIN, '📌')} প্রতি সফল OTP-এর নতুন রেট টাকা হিসেবে পাঠান (যেমন <code>1.50</code>)। বন্ধ করতে <code>0</code> পাঠান।",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
    )

@dp.callback_query(F.data == "adm_add_earning_service")
async def adm_add_earning_service_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try:
            await c.answer("❌ Access Denied!", show_alert=True)
        except Exception:
            pass
        return
    try:
        await c.answer()
    except Exception:
        pass
    uid = c.from_user.id
    state.admin_pending_actions[uid] = "add_earning_service"
    await safe_edit(
        c.message,
        f"{ce(E_OTP_KEY, '✏️')} <b>Add Service</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Send any service name. There is <b>no fixed service limit</b>.\n"
        f"Example: <code>Netflix</code>, <code>TikTok</code>, <code>Uber</code>\n\n"
        f"এরপর প্রতি সফল OTP-এর রেট টাকা হিসেবে সেট করতে পারবেন।",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
    )


@dp.callback_query(F.data == "adm_remove_earning_service")
async def adm_remove_earning_service_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    services = await get_custom_rate_services()
    state.custom_rate_svc_list = services
    if not services:
        await safe_edit(c.message, "❌ No custom earning services to remove.", await admin_rates_keyboard())
        return

    rows = []
    for idx, svc in enumerate(services):
        rows.append([btn(f"🗑 {svc_display_name(svc)}", E_BROADCAST_FAIL, callback_data=f"adm_remove_rate_c_{idx}", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
    await safe_edit(
        c.message,
        "🗑 <b>Remove Service</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select the custom service you want to remove. Built-in services cannot be removed.",
        markup(*rows)
    )

@dp.callback_query(F.data.regexp(r"^adm_remove_rate_c_(\d+)$"))
async def adm_remove_earning_service_confirm_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    idx = int(c.data.split("_")[-1])
    services = await get_custom_rate_services()
    if idx < 0 or idx >= len(services):
        await safe_edit(c.message, "❌ Session expired. Please reopen Rate.", await admin_rates_keyboard())
        return

    svc = services[idx].strip()
    svc_key = _rate_key(svc)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_FILE) as db:
        # Keep stock numbers untouched; only hide this service from Rate.
        await db.execute(
            "INSERT OR REPLACE INTO earning_services (service_name, created_at, enabled) "
            "VALUES (?, COALESCE((SELECT created_at FROM earning_services WHERE service_name = ?), ?), 0)",
            (svc, svc, now)
        )
        await db.execute("DELETE FROM bot_settings WHERE key IN (?, ?)", (f"rate_{svc_key}", f"stock_rate_{svc_key}"))
        await db.execute("DELETE FROM bot_settings WHERE key LIKE ?", (f"country_rate_{svc_key}_%",))
        await db.commit()

    state.hadi_settings.pop(f"rate_{svc_key}", None)
    state.hadi_settings.pop(f"stock_rate_{svc_key}", None)
    for key in list(state.hadi_settings.keys()):
        if key.startswith(f"country_rate_{svc_key}_") or key.startswith(f"global_country_rate_{svc_key}_"):
            state.hadi_settings.pop(key, None)
    state.admin_pending_actions.pop(c.from_user.id, None)

    await safe_edit(
        c.message,
        f"✅ <b>{html.escape(svc)}</b> earning service removed.\n\n"
        "Stock numbers were not deleted. If you add/set this service again, it will become active again.",
        await admin_rates_keyboard()
    )

@dp.callback_query(F.data.regexp(r"^adm_set_rate_c_(\d+)$"))
async def adm_set_rate_custom_prompt_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    idx = int(c.data.split("_")[-1])
    if idx < 0 or idx >= len(state.custom_rate_svc_list):
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired, please reopen Rate.", await admin_rates_keyboard())
        return
    svc = state.custom_rate_svc_list[idx]
    current = get_service_rate(svc)
    state.admin_pending_actions[c.from_user.id] = f"set_rate_c:{svc}"

    await safe_edit(
        c.message,
        f"{ce(E_OTP_KEY, '✏️')} <b>{svc_display_name(svc)} Rate</b> <i>(custom service)</i>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current rate: <b>{format_dual_rate(current)}</b> / successful OTP\n\n"
        f"{ce(E_MENU_PIN, '📌')} প্রতি সফল OTP-এর নতুন রেট টাকা হিসেবে পাঠান (যেমন <code>1.50</code>)। বন্ধ করতে <code>0</code> পাঠান।",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
    )

@dp.callback_query(F.data == "adm_settings")
async def adm_settings_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    hist_count  = sum(len(v) for v in state.otp_history.values())
    
    # Hadi database details
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM numbers") as cursor:
            stock_count = (await cursor.fetchone())[0]

    active_panels = sum(1 for p in SMS_PROVIDERS if provider_configured(p))

    text = "⚙️ <b>BOT SETTINGS</b>"
    await safe_edit(c.message, text, admin_settings_keyboard())

@dp.callback_query(F.data == "adm_support_mgmt")
async def cb_adm_support_mgmt(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    # Show ADD SUPPORT until configured, then only CHANGE SUPPORT.
    # Keep a Back button below, returning to BOT SETTING.
    username = get_support_username()
    if username:
        text = "💬 <b>SUPPORT</b>\n━━━━━━━━━━━━━━━━━━━━"
        rows = [[btn("💬 CHANGE SUPPORT", E_FWD_OK, callback_data="adm_support_change", style="danger")]]
    else:
        text = "💬 <b>ADD SUPPORT</b>\n━━━━━━━━━━━━━━━━━━━━"
        rows = [[btn("💬 ADD SUPPORT", E_FWD_OK, callback_data="adm_support_change", style="danger")]]
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_settings", style="danger")])
    await safe_edit(c.message, text, markup(*rows))


@dp.callback_query(F.data == "adm_support_change")
async def cb_adm_support_change(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    state.admin_pending_actions[c.from_user.id] = "set_support_username"
    await safe_edit(
        c.message,
        "💬 <b>Set Support Username</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the Telegram username, for example: <code>@your_support</code>\n\n"
        "This value is saved in the database and is not hardcoded in the bot.",
        markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_support_mgmt", style="danger")])
    )


@dp.callback_query(F.data == "adm_support_remove")
async def cb_adm_support_remove(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    await set_support_username(None)
    state.admin_pending_actions.pop(c.from_user.id, None)
    state.admin_temp_data.pop(c.from_user.id, None)
    try: await c.answer("Support removed", show_alert=True)
    except Exception: pass
    await cb_adm_support_mgmt(c)


# ── Panel Management: list all panels (2 per row) ──
@dp.callback_query(F.data == "adm_panel_mgmt")
async def cb_adm_panel_mgmt(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    lines = [f"{ce(E_ADMIN_TOOL, '📡')} <b>Panel Management</b>", "━━━━━━━━━━━━━━━━━━━━", "", "<b>📊 OTP received by panel</b>"]
    for provider in SMS_PROVIDERS:
        label = PROVIDER_LABELS.get(provider, provider.capitalize())
        dot = '🟢' if provider_configured(provider) else '🔴'
        today_count, lifetime_count = await get_panel_otp_counts(provider)
        lines.append(f"{dot} <b>{html.escape(label)}</b> — Today: <b>{today_count}</b> · Total: <b>{lifetime_count}</b>")
    for key, panel in CAPTCHA_PANELS.items():
        label = panel.get("label", key)
        today_count, lifetime_count = await get_panel_otp_counts(key)
        lines.append(f"🔐 <b>{html.escape(str(label))}</b> — Today: <b>{today_count}</b> · Total: <b>{lifetime_count}</b>")
    lines.append("\n<i>Counts start from when this update is deployed; earlier OTPs cannot be attributed to a panel retrospectively.</i>")
    await safe_edit(c.message, "\n".join(lines), admin_panel_mgmt_keyboard())

# ── Panel Management: single panel detail (Token/URL/Interval/Remove) ──
@dp.callback_query(F.data.regexp(r"^adm_panel_detail_([a-z0-9]+)$"))
async def cb_adm_panel_detail(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    m = re.match(r"^adm_panel_detail_([a-z0-9]+)$", c.data)
    provider = m.group(1)
    if provider not in SMS_PROVIDERS:
        try: await c.answer("Panel not found!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    label = PROVIDER_LABELS.get(provider, provider.capitalize())
    token = get_provider_token(provider)
    token_disp = f"{token[:12]}..." if token else "— not set —"
    token_lbl, url_lbl = "Token", "URL"

    text = (
        f"{ce(E_ADMIN_TOOL, '🛠')} <b>{label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Status   : {'🟢 Active' if provider_configured(provider) else '🔴 Not configured'}\n"
        f"{token_lbl:<8} : <code>{token_disp}</code>\n"
        f"{url_lbl:<8} : <code>{get_provider_url(provider) or '— not set —'}</code>\n"
        f"Interval : <b>{get_provider_interval(provider)}</b> seconds"
    )
    await safe_edit(c.message, text, admin_panel_detail_keyboard(provider))

@dp.callback_query(F.data == "adm_clear_otp_history")
async def adm_clear_otp_history_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    count = sum(len(v) for v in state.otp_history.values())
    state.otp_history.clear()
    try: await c.answer(f"{count} OTP history records cleared!", show_alert=True)
    except Exception: pass
    await adm_settings_cb(c)

# ── Add Panel wizard: Panel Name ➜ API Token ➜ API URL ──

# ── Remove a dynamically-added panel (base 3 panels can't be removed) ──
@dp.callback_query(F.data.regexp(r"^adm_rm_panel_([a-z0-9]+)$"))
async def cb_adm_rm_panel(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    m = re.match(r"^adm_rm_panel_([a-z0-9]+)$", c.data)
    key = m.group(1)
    if key not in SMS_PROVIDERS or key in BASE_SMS_PROVIDERS:
        return

    label = PROVIDER_LABELS.get(key, key.capitalize())
    await remove_dynamic_panel(key)

    try: await c.answer(f"{label} panel removed & stopped!", show_alert=True)
    except Exception: pass
    await cb_adm_panel_mgmt(c)

# ── Force Join: category list ──

# ── Force Join: master on/off toggle ──
@dp.callback_query(F.data == "adm_fj_toggle")
async def cb_adm_fj_toggle(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    await set_force_join_enabled(not force_join_enabled())
    await cb_adm_forcejoin_mgmt(c)

# ── Force Join: single channel detail (URL/Remove) ──
@dp.callback_query(F.data.regexp(r"^adm_fj_detail_([a-z0-9]+)$"))
async def cb_adm_fj_detail(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    m = re.match(r"^adm_fj_detail_([a-z0-9]+)$", c.data)
    key = m.group(1)
    ch = state.force_join_channels.get(key)
    if not ch:
        try: await c.answer("Channel not found!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    text = (
        f"{ce(E_JOIN_CHANNEL, '📢')} <b>{ch['label']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Chat ID : <code>{ch['chat_id']}</code>\n"
        f"URL     : <code>{ch['url']}</code>"
    )
    await safe_edit(c.message, text, force_join_detail_keyboard(key))

# ── Force Join: start Add Channel wizard ──
@dp.callback_query(F.data == "adm_fj_add")
async def cb_adm_fj_add(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    state.admin_temp_data[c.from_user.id] = {}
    state.admin_pending_actions[c.from_user.id] = "addfj_label"

    await safe_edit(
        c.message,
        f"{ce(E_FWD_OK, '➕')} <b>Add Force Join Channel</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} একটা নাম পাঠান (যেমন: <code>Backup Channel</code>)\n\n"
        f"<i>Step 1/3 — Name</i>",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="danger")])
    )

# ── Force Join: remove a channel (base 2 can't be removed) ──
@dp.callback_query(F.data.regexp(r"^adm_fj_rm_([a-z0-9]+)$"))
async def cb_adm_fj_rm(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    m = re.match(r"^adm_fj_rm_([a-z0-9]+)$", c.data)
    key = m.group(1)
    if key in BASE_FORCE_JOIN_KEYS or key not in state.force_join_channels:
        try: await c.answer("The default channel/group cannot be removed.", show_alert=True)
        except Exception: pass
        return

    label = state.force_join_channels.get(key, {}).get("label", key.capitalize())
    await remove_force_join_channel(key)

    try: await c.answer(f"{label} removed from Force Join!", show_alert=True)
    except Exception: pass
    await cb_adm_forcejoin_mgmt(c)

# ── Force Join: edit a channel's URL ──
@dp.callback_query(F.data.regexp(r"^adm_fj_seturl_([a-z0-9]+)$"))
async def cb_adm_fj_seturl(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    m = re.match(r"^adm_fj_seturl_([a-z0-9]+)$", c.data)
    key = m.group(1)
    if key not in state.force_join_channels:
        return
    label = state.force_join_channels[key]["label"]
    state.admin_pending_actions[c.from_user.id] = f"set_fj_url_{key}"

    await safe_edit(
        c.message,
        f"{ce(E_MENU_GLOBE2, '🗺️')} <b>{label} — Edit URL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} নতুন join URL পাঠান (যেমন: <code>https://t.me/yourchannel</code>):",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data=f"adm_fj_detail_{key}", style="danger")])
    )

# ── Auto Captcha Panel: category list ──
@dp.callback_query(F.data == "adm_captcha_mgmt")
async def cb_adm_captcha_mgmt(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    lines = [f"{ce(E_MENU_LOCK, '🔐')} <b>Auto Captcha Panel</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    _generic_panels = {k: p for k, p in CAPTCHA_PANELS.items() if (p.get("panel_type") or "generic") == "generic"}
    if not _generic_panels:
        lines.append(f"{ce(E_MENU_PIN, '📌')} <i>এখনো কোনো Auto Captcha Panel add করা হয়নি।</i>")
    for key, p in _generic_panels.items():
        dot = '🟢' if p.get("login_status", "").startswith("✅") else '🔴'
        lines.append(f"  {dot} {p.get('label', key)}")
    lines.append("")
    lines.append(f"{ce(E_MENU_PIN, '📌')} <i>এই panel গুলো username/password দিয়ে সরাসরি login করে captcha auto-solve করে SMS টেবিল পড়ে — token/API লাগে না।</i>")

    await safe_edit(c.message, "\n".join(lines), captcha_panel_mgmt_keyboard())

# ── Auto Captcha Panel: single panel detail ──
@dp.callback_query(F.data.regexp(r"^adm_cap_detail_([a-zA-Z0-9]+)$"))
async def cb_adm_cap_detail(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    m = re.match(r"^adm_cap_detail_([a-zA-Z0-9]+)$", c.data)
    key = m.group(1)
    p = CAPTCHA_PANELS.get(key)
    if not p:
        try: await c.answer("Panel not found!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    text = (
        f"{ce(E_MENU_LOCK, '🔐')} <b>{p['label']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Login Status : {p.get('login_status', 'Unknown')}\n"
        f"Login URL    : <code>{p.get('login_url', 'None')}</code>\n"
        f"Username     : <code>{p.get('username', 'None')}</code>\n"
        f"Msg Link     : <code>{p.get('msg_link') or '— auto —'}</code>\n"
        f"Num Col      : {p.get('num_col_name')} (Idx: {p.get('num_col_idx')})\n"
        f"Msg Col      : {p.get('msg_col_name')} (Idx: {p.get('msg_col_idx')})"
    )
    await safe_edit(c.message, text, captcha_panel_detail_keyboard(key))

# ── Auto Captcha Panel: manual retry login ──
@dp.callback_query(F.data.regexp(r"^adm_cap_retry_([a-zA-Z0-9]+)$"))
async def cb_adm_cap_retry(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    m = re.match(r"^adm_cap_retry_([a-zA-Z0-9]+)$", c.data)
    key = m.group(1)
    if key not in CAPTCHA_PANELS:
        try: await c.answer("Panel not found!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer("🔁 Logging in...", show_alert=False)
    except Exception: pass

    captcha_sessions.pop(key, None)
    await attempt_captcha_login(key)
    status = CAPTCHA_PANELS[key].get("login_status", "")
    try: await c.answer(status, show_alert=True)
    except Exception: pass
    await cb_adm_cap_detail(c)

# ── Auto Captcha Panel: test connection (login if needed, fetch + parse,
#    show sample OTPs, or dump the full raw table to a .txt file so the
#    admin can read off the correct column serial numbers) ──
@dp.callback_query(F.data.regexp(r"^adm_cap_test_([a-zA-Z0-9]+)$"))
async def cb_adm_cap_test(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    m = re.match(r"^adm_cap_test_([a-zA-Z0-9]+)$", c.data)
    key = m.group(1)
    if key not in CAPTCHA_PANELS:
        try: await c.answer("Panel not found!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer("🧪 Testing connection...", show_alert=False)
    except Exception: pass

    p = CAPTCHA_PANELS[key]
    wait_msg = await c.message.answer(f"{ce(E_TOOL_REFRESHING, '⏳')} Testing connection. Please wait...", parse_mode="HTML")

    parsed, raw_html = [], ""
    try:
        # Log in first if there's no active session yet.
        if key not in captcha_sessions:
            ok = await attempt_captcha_login(key)
            if not ok:
                try: await wait_msg.delete()
                except Exception: pass
                status = CAPTCHA_PANELS[key].get("login_status", "Unknown")
                await c.message.answer(
                    f"{ce(E_BROADCAST_FAIL, '❌')} <b>Auto Login Failed!</b>\nReason: <code>{html.escape(str(status))}</code>",
                    parse_mode="HTML"
                )
                return

        # Try up to 2 times — once with the existing session, once more after a fresh
        # re-login if the session turned out to be expired.
        for attempt in range(2):
            try:
                parsed, raw_html = await fetch_captcha_panel_data(key)
                break
            except Exception as sess_err:
                if "Session expired" in str(sess_err) and attempt == 0:
                    captcha_sessions.pop(key, None)
                    ok = await attempt_captcha_login(key)
                    if not ok:
                        raise
                    continue
                raise

    except Exception as e:
        try: await wait_msg.delete()
        except Exception: pass
        await c.message.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} <b>Connection Error:</b> <code>{html.escape(str(e)[:300])}</code>",
            parse_mode="HTML"
        )
        return

    try: await wait_msg.delete()
    except Exception: pass

    if parsed:
        lines = [f"{ce(E_FWD_OK, '✅')} <b>Connection Successful!</b>", "", f"🎯 <b>Parsed Data Sample (Max 3):</b>", ""]
        for i, sample in enumerate(parsed[:3]):
            lines.append(f"<b>{i+1}.</b>")
            lines.append(f"📱 Number: <code>{sample['number']}</code>")
            lines.append(f"📝 Full Msg: <code>{html.escape(str(sample['message']))}</code>")
            lines.append(f"🔐 OTP: <code>{sample['otp']}</code>")
            lines.append("➖" * 12)
        await c.message.answer("\n".join(lines), parse_mode="HTML")
        return

    # Nothing parsed — if there's at least an HTML table, dump the FULL raw table
    # (row/col serials included) into a .txt file so the admin can read off the
    # correct Num/Msg Column Serial to punch into the wizard.
    try:
        soup = BeautifulSoup(raw_html, 'html.parser')
        tables = soup.find_all('table')
    except Exception as e:
        await c.message.answer(f"{ce(E_BROADCAST_FAIL, '❌')} <b>Error parsing HTML:</b> <code>{html.escape(str(e))}</code>", parse_mode="HTML")
        return

    if not tables:
        await c.message.answer(
            f"{ce(E_ADMIN_WRENCH, '⚠️')} <b>Connected, but no HTML Table found!</b>\nMake sure the Msg Link is correct.",
            parse_mode="HTML"
        )
        return

    buf_lines = ["🔍 FULL TABLE DATA (A-Z)", "=" * 50, ""]
    for t_idx, table in enumerate(tables):
        buf_lines.append(f"--- Table {t_idx + 1} ---")
        for r_idx, row in enumerate(table.find_all('tr')):
            cols = row.find_all(['th', 'td'])
            col_texts = [f"[{c_idx + 1}] {cell.get_text(separator=' ', strip=True)}" for c_idx, cell in enumerate(cols)]
            buf_lines.append(f"Row {r_idx + 1}: {' | '.join(col_texts)}")
        buf_lines.append("")
        buf_lines.append("=" * 50)

    file_bytes = "\n".join(buf_lines).encode("utf-8")
    doc = types.BufferedInputFile(file_bytes, filename=f"Full_Panel_Data_{key}.txt")
    await c.message.answer_document(doc)
    await c.message.answer(
        f"{ce(E_ADMIN_WRENCH, '⚠️')} <b>Connected, but couldn't parse OTP data!</b>\n\n"
        f"<i>Full (A-Z) table data has been sent as a text file. Open it and check the correct "
        f"Column Serial (e.g. [1], [3]), then set it via Retry Login ➜ Remove Panel ➜ re-add with "
        f"the right Num/Msg Column Serial.</i>",
        parse_mode="HTML"
    )

# ── Auto Captcha Panel: remove ──
@dp.callback_query(F.data.regexp(r"^adm_rm_cap_panel_([a-zA-Z0-9]+)$"))
async def cb_adm_rm_cap_panel(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    m = re.match(r"^adm_rm_cap_panel_([a-zA-Z0-9]+)$", c.data)
    key = m.group(1)
    if key not in CAPTCHA_PANELS:
        return
    label = CAPTCHA_PANELS[key].get("label", key)
    await remove_captcha_panel(key)

    try: await c.answer(f"{label} panel removed & stopped!", show_alert=True)
    except Exception: pass
    await cb_adm_captcha_mgmt(c)

# ── Add Auto Captcha Panel wizard: Name ➜ Login URL ➜ Username ➜ Password ➜
#    Msg Link ➜ Num Col Name ➜ Num Col Idx ➜ Msg Col Name ➜ Msg Col Idx ──
@dp.callback_query(F.data == "adm_add_cap_panel")
async def cb_adm_add_cap_panel(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    state.admin_temp_data[c.from_user.id] = {}
    state.admin_pending_actions[c.from_user.id] = "addcap_name"

    await safe_edit(
        c.message,
        f"{ce(E_FWD_OK, '➕')} <b>Add Auto Captcha Panel</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} Panel-টার একটা নাম পাঠান (যেমন: <code>ZyronSMS</code>, <code>StexSMS</code>)\n\n"
        f"<i>Step 1/9 — Name</i>",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
    )

# ══════════════════════════════════════════════════════════════
#  GREEN PANEL (Green SMS) — আলাদা section, শুধু Name → Login URL →
#  Username → Password (কোনো column config লাগে না, JSON API auto-parse করে)
# ══════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "adm_green_mgmt")
async def cb_adm_green_mgmt(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    green_panels = {k: p for k, p in CAPTCHA_PANELS.items() if (p.get("panel_type") or "generic") == "greennews"}
    lines = [f"{ce(E_MENU_LOCK, '🟢')} <b>Green Panel</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not green_panels:
        lines.append(f"{ce(E_MENU_PIN, '📌')} <i>এখনো কোনো Green Panel add করা হয়নি।</i>")
    for key, p in green_panels.items():
        dot = '🟢' if p.get("login_status", "").startswith("✅") else '🔴'
        lines.append(f"  {dot} {p.get('label', key)}")
    lines.append("")
    lines.append(f"{ce(E_MENU_PIN, '📌')} <i>এটা Green SMS এর নিজস্ব auto captcha panel — শুধু Login URL, username আর password দিলেই bot নিজে login করে SMS পড়া শুরু করবে।</i>")

    await safe_edit(c.message, "\n".join(lines), green_panel_mgmt_keyboard())

@dp.callback_query(F.data.regexp(r"^adm_green_detail_([a-zA-Z0-9]+)$"))
async def cb_adm_green_detail(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    m = re.match(r"^adm_green_detail_([a-zA-Z0-9]+)$", c.data)
    key = m.group(1)
    p = CAPTCHA_PANELS.get(key)
    if not p or (p.get("panel_type") or "generic") != "greennews":
        try: await c.answer("Panel not found!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    text = (
        f"{ce(E_MENU_LOCK, '🟢')} <b>{p['label']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Login Status : {p.get('login_status', 'Unknown')}\n"
        f"Login URL    : <code>{p.get('login_url', 'None')}</code>\n"
        f"Username     : <code>{p.get('username', 'None')}</code>"
    )
    await safe_edit(c.message, text, green_panel_detail_keyboard(key))

@dp.callback_query(F.data.regexp(r"^adm_green_retry_([a-zA-Z0-9]+)$"))
async def cb_adm_green_retry(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    m = re.match(r"^adm_green_retry_([a-zA-Z0-9]+)$", c.data)
    key = m.group(1)
    if key not in CAPTCHA_PANELS:
        try: await c.answer("Panel not found!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer("🔁 Logging in...", show_alert=False)
    except Exception: pass

    captcha_sessions.pop(key, None)
    await attempt_captcha_login(key)
    status = CAPTCHA_PANELS[key].get("login_status", "")
    try: await c.answer(status, show_alert=True)
    except Exception: pass
    await cb_adm_green_detail(c)

@dp.callback_query(F.data.regexp(r"^adm_green_test_([a-zA-Z0-9]+)$"))
async def cb_adm_green_test(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    m = re.match(r"^adm_green_test_([a-zA-Z0-9]+)$", c.data)
    key = m.group(1)
    if key not in CAPTCHA_PANELS:
        try: await c.answer("Panel not found!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer("🧪 Testing connection...", show_alert=False)
    except Exception: pass

    wait_msg = await c.message.answer(f"{ce(E_TOOL_REFRESHING, '⏳')} Testing connection. Please wait...", parse_mode="HTML")

    parsed = []
    try:
        if key not in captcha_sessions:
            ok = await attempt_captcha_login(key)
            if not ok:
                try: await wait_msg.delete()
                except Exception: pass
                status = CAPTCHA_PANELS[key].get("login_status", "Unknown")
                await c.message.answer(
                    f"{ce(E_BROADCAST_FAIL, '❌')} <b>Auto Login Failed!</b>\nReason: <code>{html.escape(str(status))}</code>",
                    parse_mode="HTML"
                )
                return

        for attempt in range(2):
            try:
                parsed, _raw = await fetch_captcha_panel_data(key)
                break
            except Exception as sess_err:
                if "Session expired" in str(sess_err) and attempt == 0:
                    captcha_sessions.pop(key, None)
                    ok = await attempt_captcha_login(key)
                    if not ok:
                        raise
                    continue
                raise
    except Exception as e:
        try: await wait_msg.delete()
        except Exception: pass
        await c.message.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} <b>Connection Error:</b> <code>{html.escape(str(e)[:300])}</code>",
            parse_mode="HTML"
        )
        return

    try: await wait_msg.delete()
    except Exception: pass

    if parsed:
        lines = [f"{ce(E_FWD_OK, '✅')} <b>Connection Successful!</b>", "", f"🎯 <b>Parsed Data Sample (Max 3):</b>", ""]
        for i, sample in enumerate(parsed[:3]):
            lines.append(f"<b>{i+1}.</b>")
            lines.append(f"📱 Number: <code>{sample['number']}</code>")
            lines.append(f"📝 Full Msg: <code>{html.escape(str(sample['message']))}</code>")
            lines.append(f"🔐 OTP: <code>{sample['otp']}</code>")
            lines.append("➖" * 12)
        await c.message.answer("\n".join(lines), parse_mode="HTML")
    else:
        await c.message.answer(
            f"{ce(E_FWD_OK, '✅')} <b>Login OK — কিন্তু এখনো কোনো নতুন SMS নেই।</b>\n"
            f"<i>নতুন OTP আসলেই বট নিজে ধরে ফেলবে, restart লাগবে না।</i>",
            parse_mode="HTML"
        )

@dp.callback_query(F.data.regexp(r"^adm_rm_green_panel_([a-zA-Z0-9]+)$"))
async def cb_adm_rm_green_panel(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    m = re.match(r"^adm_rm_green_panel_([a-zA-Z0-9]+)$", c.data)
    key = m.group(1)
    if key not in CAPTCHA_PANELS:
        return
    label = CAPTCHA_PANELS[key].get("label", key)
    await remove_captcha_panel(key)

    try: await c.answer(f"{label} panel removed & stopped!", show_alert=True)
    except Exception: pass
    await cb_adm_green_mgmt(c)

# ── Add Green Panel wizard: Name ➜ Login URL ➜ Username ➜ Password ──
@dp.callback_query(F.data == "adm_add_green_panel")
async def cb_adm_add_green_panel(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    state.admin_temp_data[c.from_user.id] = {}
    state.admin_pending_actions[c.from_user.id] = "addgp_name"

    await safe_edit(
        c.message,
        f"{ce(E_FWD_OK, '➕')} <b>Add Green Panel</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} Panel-টার একটা নাম পাঠান (যেমন: <code>GreenSMS</code>)\n\n"
        f"<i>Step 1/4 — Name</i>",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="danger")])
    )

@dp.callback_query(F.data == "adm_toggle_channel_stock_alert")
async def cb_adm_toggle_channel_stock_alert(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    new_state = not is_channel_stock_alert_enabled()
    await set_channel_stock_alert_enabled(new_state)
    try: await c.answer(f"Channel Stock Alert: {'ON' if new_state else 'OFF'}", show_alert=True)
    except Exception: pass
    await safe_edit(c.message, "⚙️ <b>BOT SETTINGS</b>", admin_settings_keyboard())

@dp.callback_query(F.data == "adm_toggle_bot_stock_alert")
async def cb_adm_toggle_bot_stock_alert(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    new_state = not is_bot_stock_alert_enabled()
    await set_bot_stock_alert_enabled(new_state)
    try: await c.answer(f"Bot Stock Alert: {'ON' if new_state else 'OFF'}", show_alert=True)
    except Exception: pass
    await safe_edit(c.message, "⚙️ <b>BOT SETTINGS</b>", admin_settings_keyboard())

# ── SMS Hadi Upload Stock ──
@dp.callback_query(F.data == "adm_upload_stock")
async def cb_adm_upload_stock(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass
    
    uid = c.from_user.id
    await _delete_admin_flow_message(uid, "status_id")
    state.admin_pending_actions[uid] = 'upload_stock'
    await safe_edit(
        c.message,
        f"📁 <b>Upload Stock</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Send your: <code>txt</code> or <code>.xlsx</code> stock file\n\n"
        f"Auto-broadcast to your all member",
        markup([btn("Cancel ❌", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
    )
    await _track_admin_flow_message(uid, "prompt_id", c.message)

# ── Admin Forward Pool wizard ──
@dp.callback_query(F.data == "adm_forward_pool_upload")
async def cb_adm_forward_pool_upload(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    uid = c.from_user.id
    state.admin_temp_data[uid] = {}
    state.admin_pending_actions[uid] = "forward_pool_upload"
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_BOLT, '📁')} <b>Number Forward Pool</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"একটি <code>.txt</code> ফাইল পাঠান। ফাইলের প্রতিটি লাইনে একটি করে phone number রাখুন।\n"
        f"প্রতিটি নম্বরের country আলাদাভাবে auto-detect হবে; mixed-country file-ও চলবে।",
        markup([btn("✖", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
    )

@dp.callback_query(F.data == "adm_forward_pool_list")
async def cb_adm_forward_pool_list(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """SELECT p.id, p.service, p.interval_seconds, p.status, COUNT(n.phone_number)
               FROM forward_pools p
               LEFT JOIN forward_pool_numbers n ON n.pool_id = p.id
               GROUP BY p.id
               ORDER BY p.id DESC"""
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await safe_edit(
            c.message,
            f"{ce(E_ADMIN_BOLT, '📦')} <b>Active Forward Pools</b>\n\nকোনো forward pool এখনো তৈরি হয়নি।",
            markup([btn("Create Pool", E_FWD_OK, callback_data="adm_forward_pool_upload", style="danger")],
                   [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_cat_stock", style="danger")])
        )
        return

    text_lines = [f"{ce(E_ADMIN_BOLT, '📦')} <b>Number Forward Pools</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    kb_rows = []
    for pool_id, service, interval, status, count in rows:
        status_label = "🟢 Active" if status == "active" else "⚪ Stopped"
        text_lines.append(f"#{pool_id} · <b>{html.escape(svc_display_name(service))}</b> · {count} numbers · {interval}s · {status_label}")
        if status == "active":
            kb_rows.append([btn(f"Stop #{pool_id} · {svc_display_name(service)}", E_BROADCAST_FAIL, callback_data=f"fpool_stop_{pool_id}", style="danger")])
    kb_rows.append([btn("Create New Pool", E_FWD_OK, callback_data="adm_forward_pool_upload", style="danger")])
    kb_rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_cat_stock", style="danger")])
    await safe_edit(c.message, "\n".join(text_lines), {"inline_keyboard": kb_rows})

@dp.callback_query(F.data.startswith("fpool_lang_"))
async def cb_forward_pool_language(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    uid = c.from_user.id
    temp = state.admin_temp_data.get(uid)
    if not temp or "numbers" not in temp:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired. Please upload the file again.")
        return

    code = c.data[len("fpool_lang_"):]
    if code == "done":
        selected = temp.get("languages", [])
        if not selected:
            await c.answer("Select at least one language.", show_alert=True)
            return
        await c.answer()
        await safe_edit(
            c.message,
            f"{ce(E_FWD_OK, '✅')} <b>Forward Pool Ready</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Numbers: <b>{len(temp['numbers'])}</b>\n"
            f"Service: <b>{html.escape(svc_display_name(temp['service']))}</b>\n"
            f"Interval: <b>{temp['interval_seconds']} seconds</b>\n"
            f"Languages: <b>{', '.join(FORWARD_POOL_LANGUAGE_LABELS[x] for x in selected)}</b>\n\n"
            f"Start করলে এই নম্বরগুলোর নতুন OTP নির্ধারিত group-এ forward হবে।",
            markup([btn("Start Now", E_FWD_OK, callback_data="fpool_start", style="danger")],
                   [btn("Cancel", E_BROADCAST_FAIL, callback_data="admin_panel", style="danger")])
        )
        return

    if code not in FORWARD_POOL_LANGUAGE_LABELS:
        await c.answer("Invalid language.", show_alert=True)
        return
    selected = list(temp.get("languages", []))
    if code in selected:
        selected.remove(code)
    else:
        selected.append(code)
    temp["languages"] = selected
    try: await c.answer()
    except Exception: pass
    await safe_edit(
        c.message,
        f"{ce(E_MENU_PIN, '📍')} <b>Select Languages for Forward Card</b>\n\n"
        f"Selected: <b>{', '.join(FORWARD_POOL_LANGUAGE_LABELS[x] for x in selected) or 'None'}</b>",
        forward_pool_language_keyboard(selected)
    )

@dp.callback_query(F.data == "fpool_start")
async def cb_forward_pool_start(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    uid = c.from_user.id
    temp = state.admin_temp_data.pop(uid, None)
    state.admin_pending_actions.pop(uid, None)
    if not temp or not temp.get("numbers") or not temp.get("languages"):
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired. Please upload the file again.")
        return

    try:
        pool_id = await create_forward_pool(
            temp["service"],
            temp["interval_seconds"],
            temp["languages"],
            temp["numbers"],
            uid,
        )
        task = asyncio.create_task(forward_pool_monitor(pool_id), name=f"forward_pool_{pool_id}")
        state.forward_pool_tasks[pool_id] = task
    except Exception as err:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Could not start pool: <code>{html.escape(str(err))}</code>")
        return

    by_country = {}
    for item in temp["numbers"]:
        by_country[item["country"]] = by_country.get(item["country"], 0) + 1
    country_summary = ", ".join(f"{html.escape(name)} ({count})" for name, count in sorted(by_country.items()))
    target = OTP_GROUP_ID
    target_note = (
        f"Forward target: <code>{html.escape(str(target))}</code>"
        if target else
        "⚠️ Forward target is not configured yet. Set OTP_GROUP_ID before OTPs arrive."
    )
    try: await c.answer("Forward pool started.")
    except Exception: pass
    await safe_edit(
        c.message,
        f"{ce(E_FWD_OK, '✅')} <b>Forward Pool #{pool_id} Started</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Service: <b>{html.escape(svc_display_name(temp['service']))}</b>\n"
        f"Numbers active: <b>{len(temp['numbers'])}</b>\n"
        f"Countries: <b>{country_summary}</b>\n"
        f"Check interval: <b>{temp['interval_seconds']} seconds</b>\n"
        f"Languages: <b>{', '.join(FORWARD_POOL_LANGUAGE_LABELS[x] for x in temp['languages'])}</b>\n\n"
        f"{target_note}",
        markup([btn("Stop This Pool", E_BROADCAST_FAIL, callback_data=f"fpool_stop_{pool_id}", style="danger")],
               [btn("View Pools", E_ADMIN_BOLT, callback_data="adm_forward_pool_list", style="danger")])
    )

@dp.callback_query(F.data.startswith("fpool_stop_"))
async def cb_forward_pool_stop(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try:
        pool_id = int(c.data[len("fpool_stop_"):])
    except ValueError:
        await c.answer("Invalid pool.", show_alert=True)
        return
    await stop_forward_pool(pool_id)
    try: await c.answer("Pool stopped.", show_alert=True)
    except Exception: pass
    await cb_adm_forward_pool_list(c)

# ── Delete a single Number Stock entry ──
@dp.callback_query(F.data == "adm_delete_number")
async def cb_adm_delete_number(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        return
    try:
        await c.answer()
    except Exception:
        pass
    state.admin_pending_actions[c.from_user.id] = "delete_number_stock"
    await safe_edit(
        c.message,
        f"{ce(E_BROADCAST_FAIL, '🗑️')} <b>Delete Number Stock</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} Send the exact phone number you want to delete from stock.\n\n"
        f"<i>Only that number will be deleted; other stock remains unchanged.</i>",
        markup([btn("✖", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
    )

# ── Direct Stock List / Delete Stock ──
DELETE_STOCK_PAGE_SIZE = 25

async def _render_delete_stock_page(message: types.Message, page: int = 0):
    """Step 1: show services only. Countries are shown after a service is selected."""
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """
            SELECT service,
                   COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END), 0) AS available
            FROM numbers
            WHERE service IS NOT NULL AND TRIM(service) != ''
            GROUP BY service
            ORDER BY service COLLATE NOCASE
            """
        ) as cursor:
            rows = await cursor.fetchall()

    state.delstock_services = [str(r[0] or '') for r in rows]
    state.delstock_countries = []
    state.delstock_pairs = []

    if not rows:
        text = (
            "🗑️ <b>Delete Stock</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📦 No stock available."
        )
        keyboard = markup(
            [btn("📥 Download Unused Numbers", E_ADMIN_BOLT, callback_data="adm_download_unused", style="danger")],
            [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")],
        )
        await safe_edit(message, text, keyboard)
        return

    text = (
        "🗑️ <b>Delete Stock</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 Select Service 👇"
    )

    kb_rows = []
    service_buttons = []
    for idx, (service, total, available) in enumerate(rows):
        label = f"{svc_display_name(service)}  —  {available}/{total}"
        service_buttons.append(
            btn(label, E_ADMIN_WRENCH, callback_data=f"adm_delservice_{idx}", style="danger")
        )
    # Delete Stock services are shown vertically: one service per row.
    for button in service_buttons:
        kb_rows.append([button])

    kb_rows.append([
        btn("📥 Download Unused Numbers", E_ADMIN_BOLT, callback_data="adm_download_unused", style="danger")
    ])
    # Back button stays on its own final row.
    kb_rows.append([
        btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")
    ])
    await safe_edit(message, text, {"inline_keyboard": kb_rows})

@dp.callback_query(F.data == "adm_delete_stock")
async def cb_adm_delete_stock(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        return
    try:
        await c.answer()
    except Exception:
        pass
    await _render_delete_stock_page(c.message, 0)

@dp.callback_query(F.data.startswith("adm_delservice_"))
async def cb_adm_delservice(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        return
    try:
        await c.answer()
    except Exception:
        pass

    try:
        idx = int(c.data.rsplit("_", 1)[1])
    except Exception:
        try:
            await c.answer("Invalid service selection.", show_alert=True)
        except Exception:
            pass
        return

    if idx < 0 or idx >= len(state.delstock_services):
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} This service list has expired. Please open Delete Stock again.",
            admin_keyboard()
        )
        return

    service = state.delstock_services[idx]

    # Step 2: after service selection, show ONLY countries belonging to that service.
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """
            SELECT country,
                   COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END), 0) AS available
            FROM numbers
            WHERE service = ?
            GROUP BY country
            ORDER BY country COLLATE NOCASE
            """,
            (service,)
        ) as cursor:
            rows = await cursor.fetchall()

    state.delstock_countries = [str(r[0] or 'Global') for r in rows]
    state.delstock_pairs = [(country, service) for country in state.delstock_countries]

    if not rows:
        await safe_edit(
            c.message,
            f"🗑️ <b>{html.escape(svc_display_name(service))}</b>\n\n📦 No country/stock found for this service.",
            markup([btn("↩️ Back to Services", E_TOOL_BACKBUTTON, callback_data="adm_delete_stock", style="danger")])
        )
        return

    text = (
        "🗑️ <b>Delete Stock</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <b>Service:</b> {html.escape(svc_display_name(service))}\n"
        "🌍 <b>Select Country</b>\n"
        "শুধু এই Service-এর Country গুলো দেখানো হচ্ছে:"
    )

    kb_rows = []
    country_buttons = []
    for idx, (country, total, available) in enumerate(rows):
        label = f"{country}  —  {available}/{total}"
        country_buttons.append(
            btn(label, E_ADMIN_WRENCH, callback_data=f"adm_delcountry_{idx}", style="danger")
        )
    for i in range(0, len(country_buttons), 2):
        kb_rows.append(country_buttons[i:i + 2])

    kb_rows.append([
        btn("↩️ Back to Services", E_TOOL_BACKBUTTON, callback_data="adm_delete_stock", style="danger")
    ])
    await safe_edit(c.message, text, {"inline_keyboard": kb_rows})

@dp.callback_query(F.data.startswith("adm_delcountry_"))
async def cb_adm_delcountry(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        return
    try:
        await c.answer()
    except Exception:
        pass

    try:
        idx = int(c.data.rsplit("_", 1)[1])
    except Exception:
        try:
            await c.answer("Invalid country selection.", show_alert=True)
        except Exception:
            pass
        return

    if idx < 0 or idx >= len(state.delstock_pairs):
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} This country list has expired. Please open Delete Stock again.",
            admin_keyboard()
        )
        return

    country, service = state.delstock_pairs[idx]

    # IMPORTANT: selecting a country never deletes immediately; confirmation is required.
    await safe_edit(
        c.message,
        f"⚠️ <b>Confirm Delete Stock</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <b>Service:</b> {html.escape(svc_display_name(service))}\n"
        f"🌍 <b>Country:</b> {html.escape(str(country))}",
        markup(
            [btn("🗑️ Delete Stock", E_ADMIN_WRENCH, callback_data=f"adm_delconfirm_{idx}", style="danger")],
            [btn("↩️ Back to Countries", E_TOOL_BACKBUTTON, callback_data=f"adm_delservice_back_{idx}", style="danger")]
        )
    )
    return

@dp.callback_query(F.data.startswith("adm_delservice_back_"))
async def cb_adm_delservice_back(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        return
    try:
        await c.answer()
    except Exception:
        pass
    try:
        idx = int(c.data.rsplit("_", 1)[1])
    except Exception:
        idx = -1

    if idx < 0 or idx >= len(state.delstock_pairs):
        await _render_delete_stock_page(c.message, 0)
        return
    service = state.delstock_pairs[idx][1]

    # Rebuild the service's country list so this back button is always fresh.
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """
            SELECT country,
                   COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END), 0) AS available
            FROM numbers
            WHERE service = ?
            GROUP BY country
            ORDER BY country COLLATE NOCASE
            """,
            (service,)
        ) as cursor:
            rows = await cursor.fetchall()

    state.delstock_countries = [str(r[0] or 'Global') for r in rows]
    state.delstock_pairs = [(country, service) for country in state.delstock_countries]

    country_buttons = [
        btn(f"{country}  —  {available}/{total}", E_ADMIN_WRENCH, callback_data=f"adm_delcountry_{i}", style="danger")
        for i, (country, total, available) in enumerate(rows)
    ]
    kb_rows = []
    for i in range(0, len(country_buttons), 2):
        kb_rows.append(country_buttons[i:i + 2])
    kb_rows.append([btn("↩️ Back to Services", E_TOOL_BACKBUTTON, callback_data="adm_delete_stock", style="danger")])
    await safe_edit(
        c.message,
        "🗑️ <b>Delete Stock</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <b>Service:</b> {html.escape(svc_display_name(service))}\n"
        "🌍 <b>Select Country</b>",
        {"inline_keyboard": kb_rows}
    )

@dp.callback_query(F.data.startswith("adm_delconfirm_"))
async def cb_adm_delconfirm(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        return
    try:
        await c.answer()
    except Exception:
        pass

    try:
        idx = int(c.data.rsplit("_", 1)[1])
    except Exception:
        try:
            await c.answer("Invalid confirmation.", show_alert=True)
        except Exception:
            pass
        return

    if idx < 0 or idx >= len(state.delstock_pairs):
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} This stock selection has expired. Please open Delete Stock again.",
            admin_keyboard()
        )
        return

    country, service = state.delstock_pairs[idx]

    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "DELETE FROM numbers WHERE country = ? AND service = ?",
            (country, service)
        )
        deleted = max(0, cursor.rowcount)
        await db.commit()

    if deleted:
        try:
            await c.answer(f"🗑️ Deleted {deleted} numbers successfully.", show_alert=True)
        except Exception:
            pass
    else:
        try:
            await c.answer("⚠️ That stock is already empty.", show_alert=True)
        except Exception:
            pass

    # After deletion, return to the service list so the whole flow starts cleanly.
    await _render_delete_stock_page(c.message, 0)

# ── Download Unused Stock ──
@dp.callback_query(F.data == "adm_download_unused")
async def cb_adm_download_unused(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    # STEP 1: show services first.
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT service, COUNT(*) FROM numbers WHERE status = 'available' "
            "GROUP BY service ORDER BY service COLLATE NOCASE"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} No unused stock numbers available right now.",
            admin_keyboard()
        )
        return

    state.dlunused_service_list = [str(r[0] or "Unknown").strip() or "Unknown" for r in rows]
    state.dlunused_country_list = []

    kb_rows = []
    service_buttons = []
    for idx, (service, count) in enumerate(rows):
        service_text = str(service or "Unknown")
        service_buttons.append(
            btn(
                f"{svc_display_name(service_text)}  —  {count}",
                E_ADMIN_BOLT,
                callback_data=f"adm_dlunused_service_{idx}",
                style="danger"
            )
        )
    for i in range(0, len(service_buttons), 2):
        kb_rows.append(service_buttons[i:i + 2])
    kb_rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_delete_stock", style="danger")])

    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_BOLT, '📦')} <b>Download Unused Stock</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Select Service 👇",
        {"inline_keyboard": kb_rows}
    )


@dp.callback_query(F.data.startswith("adm_dlunused_service_"))
async def cb_adm_dlunused_service(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    idx_str = c.data[len("adm_dlunused_service_"):]
    try:
        idx = int(idx_str)
    except ValueError:
        return

    if idx < 0 or idx >= len(state.dlunused_service_list):
        await c.message.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} Selection expired — please open Download Unused Stock again.",
            parse_mode="HTML"
        )
        return

    service = state.dlunused_service_list[idx]
    state.dlunused_selected_service = service

    # STEP 2: after service selection, show only countries that have
    # available/unused numbers for that service.
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT country, COUNT(*) FROM numbers "
            "WHERE status = 'available' AND service = ? "
            "GROUP BY country ORDER BY country COLLATE NOCASE",
            (service,)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await c.message.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} No unused numbers found for {html.escape(svc_display_name(service))}.",
            parse_mode="HTML"
        )
        return

    state.dlunused_country_list = [str(r[0] or "Global") for r in rows]

    kb_rows = []
    country_buttons = []
    for country_idx, (country, count) in enumerate(rows):
        country_text = str(country or "Global")
        country_buttons.append(
            btn(
                f"{country_text}  —  {count}",
                E_ADMIN_BOLT,
                callback_data=f"adm_dlunused_country_{country_idx}",
                style="danger"
            )
        )
    for i in range(0, len(country_buttons), 2):
        kb_rows.append(country_buttons[i:i + 2])
    kb_rows.append([
        btn("↩️ Back to Services", E_TOOL_BACKBUTTON, callback_data="adm_download_unused", style="danger")
    ])

    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_BOLT, '📦')} <b>Download Unused Stock</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <b>Service:</b> {html.escape(svc_display_name(service))}\n"
        f"{ce(E_MENU_PIN, '📌')} এখন এই Service-এর Country নির্বাচন করুন:",
        {"inline_keyboard": kb_rows}
    )


async def _send_unused_stock_files(c: types.CallbackQuery, country: str, service: str):
    """
    Download unused stock for exactly one country + one service.

    File format:
      Country-----Service
      number
      number
      ...
    """
    query = (
        "SELECT phone_number FROM numbers "
        "WHERE status = 'available' AND country = ? AND service = ? "
        "ORDER BY phone_number"
    )

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(query, (country, service)) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await c.message.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} No unused stock found for "
            f"{html.escape(str(country))} — {html.escape(svc_display_name(service))}.",
            parse_mode="HTML"
        )
        return

    buf = io.StringIO()
    buf.write(f"{country}-----{service}\n")
    for (phone_number,) in rows:
        buf.write(f"{phone_number}\n")
    file_bytes = buf.getvalue().encode("utf-8")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_country = re.sub(r"[^A-Za-z0-9_-]+", "_", str(country)).strip("_") or "Country"
    safe_service = re.sub(r"[^A-Za-z0-9_-]+", "_", str(service)).strip("_") or "Service"
    filename = f"unused_stock_{safe_country}_{safe_service}_{timestamp}.csv"
    doc = types.BufferedInputFile(file_bytes, filename=filename)

    # Telegram caption is also only one line: Country-----Service.
    caption = f"<b>{html.escape(str(country))}-----{html.escape(str(service))}</b>"
    await c.message.answer_document(doc, caption=caption, parse_mode="HTML")


@dp.callback_query(F.data.startswith("adm_dlunused_country_"))
async def cb_adm_dlunused_country(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer("Preparing file...")
    except Exception: pass

    idx_str = c.data[len("adm_dlunused_country_"):]
    try:
        idx = int(idx_str)
    except ValueError:
        return

    if idx < 0 or idx >= len(state.dlunused_country_list):
        await c.message.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} Selection expired — please open Download Unused Stock again.",
            parse_mode="HTML"
        )
        return

    # The currently selected service is stored by the service-step index.
    service = None
    # Resolve it from the message flow by matching the selected country is not safe,
    # so keep the selected service in a dedicated state attribute.
    service = getattr(state, "dlunused_selected_service", None)
    if not service:
        await c.message.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} Service selection expired — please open Download Unused Stock again.",
            parse_mode="HTML"
        )
        return

    country = state.dlunused_country_list[idx]
    await _send_unused_stock_files(c, country, service)

@dp.callback_query(F.data.regexp(r"^adm_set_([a-z0-9]+)_(token|url|interval)$"))
async def cb_adm_set_provider(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    m = re.match(r"^adm_set_([a-z0-9]+)_(token|url|interval)$", c.data)
    provider, field = m.group(1), m.group(2)
    if provider not in SMS_PROVIDERS:
        return
    plabel = PROVIDER_LABELS.get(provider, provider.capitalize())

    labels = {
        "token":    (f"{plabel} API Token", f"Send your new {plabel} API token below:"),
        "url":      (f"{plabel} API URL",   f"Send your new {plabel} Viewstats URL below:"),
        "interval": (f"{plabel} Interval",  "Send check interval in seconds (e.g. 1):")
    }

    label, prompt = labels[field]
    state.admin_pending_actions[c.from_user.id] = f"set_{provider}_{field}"

    await safe_edit(
        c.message,
        f"{ce(E_OTP_KEY, '✏️')} <b>{label}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{ce(E_MENU_PIN, '📌')} {prompt}",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data=f"adm_panel_detail_{provider}", style="danger")])
    )

# ── Shared helper: insert/update stock numbers with given service ──
async def _save_stock_numbers(numbers, service_name, country_name, upload_rate=None) -> int:
    """Save one uploaded file for one service. With the composite key, the
    exact same phone file can safely be attached to multiple services.
    """
    added_count = 0
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("BEGIN")
        try:
            for item in numbers or []:
                try:
                    if isinstance(item, dict):
                        num = clean_number(item.get("number"))
                        item_country = str(item.get("country") or country_name or "Global").strip() or "Global"
                    else:
                        num = clean_number(item)
                        item_country = str(country_name or "Global").strip() or "Global"
                    item_rate = upload_rate
                    if not num or not service_name:
                        continue
                    if item_rate not in (None, ""):
                        try:
                            item_rate = float(item_rate)
                            if item_rate < 0:
                                item_rate = None
                        except (TypeError, ValueError):
                            item_rate = None

                    cur = await db.execute(
                        "SELECT status, assigned_to, assign_time, otp_received, sms_seen_count FROM numbers WHERE phone_number = ? AND service = ? LIMIT 1",
                        (num, service_name),
                    )
                    exists = await cur.fetchone()
                    if exists:
                        await db.execute(
                            """UPDATE numbers
                               SET country = ?, otp_rate = ?,
                                   status = CASE WHEN status IN ('busy', 'retired') THEN status
                                                 WHEN EXISTS (SELECT 1 FROM number_usage_history h WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')) THEN 'retired'
                                                 ELSE 'available' END,
                                   assigned_to = CASE WHEN status = 'busy' THEN assigned_to ELSE NULL END,
                                   assign_time = CASE WHEN status = 'busy' THEN assign_time ELSE NULL END,
                                   otp_received = CASE WHEN status = 'busy' THEN otp_received ELSE 0 END,
                                   sms_seen_count = CASE WHEN status = 'busy' THEN sms_seen_count ELSE 0 END
                             WHERE phone_number = ? AND service = ?""",
                            (item_country, item_rate, num, service_name),
                        )
                    else:
                        # If this phone is currently leased/retired in another
                        # service, keep the new service copy unavailable too.
                        cur2 = await db.execute(
                            "SELECT status, assigned_to, assign_time, otp_received, sms_seen_count FROM numbers WHERE phone_number = ? ORDER BY rowid LIMIT 1",
                            (num,),
                        )
                        old = await cur2.fetchone()
                        if old and old[0] in ('busy', 'retired'):
                            status, assigned_to, assign_time, otp_received, sms_seen_count = old
                        else:
                            status, assigned_to, assign_time, otp_received, sms_seen_count = 'available', None, None, 0, 0
                        await db.execute(
                            "INSERT INTO numbers (phone_number, service, country, otp_rate, status, assigned_to, assign_time, otp_received, sms_seen_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (num, service_name, item_country, item_rate, status, assigned_to, assign_time, otp_received, sms_seen_count),
                        )
                    added_count += 1
                except Exception as row_err:
                    print(f"[StockUpload] Skipping bad row: {row_err}")
                    continue
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return added_count

# ── Shared helper: after OTP rate is set (or skipped), persist the numbers,
#    confirm to the admin, and broadcast — used by both the button-selected
#    and custom-typed service paths so the flow is identical either way. ──
async def _finalize_stock_upload(m: types.Message, temp_data: dict):
    # Remove every tracked temporary upload-flow message before showing the
    # final result. The final Stock upload completed message is intentionally
    # left visible.
    await _delete_all_admin_flow_messages(m.from_user.id)
    numbers = temp_data["numbers"]
    country_name = temp_data["country"]
    services = list(temp_data.get("services") or [])
    if not services and temp_data.get("service"):
        services = [temp_data["service"]]
    is_custom = temp_data.get("custom", False)
    upload_rate = temp_data.get("upload_rate")
    if upload_rate is None:
        raise ValueError("Stock upload rate was not set")

    results = []
    rate = float(upload_rate)
    for service_name in services:
        added_count = await _save_stock_numbers(numbers, service_name, country_name, upload_rate=upload_rate)
        results.append((service_name, added_count))
        await _broadcast_new_stock(service_name, country_name, added_count, rate)

    service_lines = "\n".join(
        f"• {html.escape(svc_display_name(svc))}: <b>{count}</b> numbers"
        for svc, count in results
    )
    await m.answer(
        f"{ce(E_FWD_OK, '✅')} Stock upload completed!\n\n"
        f"{ce(E_ADMIN_TOOL, '🛠')} Services: <b>{len(services)}</b>{' <i>(custom)</i>' if is_custom else ''}\n"
        f"{service_lines}\n\n"
        f"{ce(E_OTP_GLOBE, '🏳️')} Country: <b>{html.escape(country_name)}</b>\n"
        f"{ce(E_ADMIN_CASH, '💰')} OTP Rate: <b>{format_dual_rate(rate)}</b>",
        reply_markup=admin_keyboard()
    )

# ── Shared helper: broadcast "new stock added" message to known users ──

# ── Stock Numbers allocation service callbacks ──
@dp.callback_query(F.data.startswith("adm_uploadsvc_"))
async def cb_adm_uploadsvc(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    uid = c.from_user.id
    try: await c.answer()
    except Exception: pass

    temp = state.admin_temp_data.get(uid)
    if not temp or "numbers" not in temp:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please re-upload.")
        return

    action = c.data[len("adm_uploadsvc_"):]
    selected = list(temp.get("services") or [])

    if action == "done":
        if not selected:
            await c.answer("Select at least one service.", show_alert=True)
            return
        temp["services"] = selected
        temp["service"] = selected[0]
        temp["custom"] = False
        state.admin_pending_actions[uid] = "upload_stock_rate"
        await safe_edit(
            c.message,
            f"{ce(E_ADMIN_CASH, '💰')} <b>Set OTP Rate</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Services: <b>{len(selected)}</b>\n"
            f"<code>{html.escape(', '.join(svc_display_name(x) for x in selected))}</code>\n\n"
            f"এই upload-এর সব number-এর প্রতি successful OTP-এর রেট টাকা হিসেবে পাঠান।\n"
            f"উদাহরণ: <code>1.25</code> টাকা",
            markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_upload_stock", style="danger")])
        )
        return

    if action.startswith("sel_"):
        try:
            idx = int(action[4:])
            service_name = ALLOWED_SERVICES[idx]
        except Exception:
            await c.answer("Invalid service", show_alert=True); return
        if service_name in selected:
            selected.remove(service_name)
        else:
            selected.append(service_name)
        temp["services"] = selected

        rows = []
        for i, svc in enumerate(ALLOWED_SERVICES):
            mark = "☑️" if svc in selected else "⬜"
            rows.append([btn(f"{mark} {svc_display_name(svc)}", E_NUM_SERVICE, callback_data=f"adm_uploadsvc_sel_{i}", style="danger" if svc in selected else "danger")])
        rows.append([btn(f"✅ Done ({len(selected)})", E_FWD_OK, callback_data="adm_uploadsvc_done", style="danger")])
        rows.append([btn("Custom Service (Type Name)", E_ADMIN_TOOL, callback_data="adm_stock_custom_svc", style="danger")])
        rows.append([btn("Cancel", E_BROADCAST_FAIL, callback_data="admin_panel", style="danger")])
        await safe_edit(
            c.message,
            f"{ce(E_RK_GET_NUM, '📱')} Found <b>{len(temp['numbers'])}</b> valid phone numbers.\n\n"
            f"<b>Select one or more services</b> — একই file একাধিক service-এ upload হবে.\n"
            f"Selected: <b>{len(selected)}</b>",
            {"inline_keyboard": rows}
        )
        return

    # Backward-compatible direct service click: select it and stay on the multi-select screen.
    service_name = action
    if service_name in ALLOWED_SERVICES:
        if service_name in selected: selected.remove(service_name)
        else: selected.append(service_name)
        temp["services"] = selected
        # Reuse the same rendering branch.
        rows = []
        for i, svc in enumerate(ALLOWED_SERVICES):
            mark = "☑️" if svc in selected else "⬜"
            rows.append([btn(f"{mark} {svc_display_name(svc)}", E_NUM_SERVICE, callback_data=f"adm_uploadsvc_sel_{i}", style="danger" if svc in selected else "danger")])
        rows.append([btn(f"✅ Done ({len(selected)})", E_FWD_OK, callback_data="adm_uploadsvc_done", style="danger")])
        rows.append([btn("Cancel", E_BROADCAST_FAIL, callback_data="admin_panel", style="danger")])
        await safe_edit(c.message, f"<b>Select one or more services</b>\nSelected: <b>{len(selected)}</b>", {"inline_keyboard": rows})
        return


# ── Stock Numbers allocation: CUSTOM (manually typed) service name ──
@dp.callback_query(F.data == "adm_stock_custom_svc")
async def cb_adm_stock_custom_svc(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass
    
    uid = c.from_user.id
    if uid not in state.admin_temp_data:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please re-upload.")
        return
    
    state.admin_pending_actions[uid] = 'upload_stock_custom_svc'
    
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_TOOL, '✏️')} <b>Type the Custom Service Name</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Example: <code>Facebook1</code>, <code>New Fb</code>, <code>Netflix</code>, <code>PayPal</code>\n\n"
        f"{ce(E_MENU_PIN, '📌')} <i>Send the service name as a text message now.\n"
        f"OTP Rate set korar por apnake ekta list dekhano hobe, jekhan theke "
        f"select korben eita আসলে kon service-er number (jemon Facebook) — "
        f"shei service er official emoji e OTP Forward Card e show hobe.</i>",
        markup([btn("Close", E_BROADCAST_FAIL, callback_data="close_menu", style="danger")])
    )

# ── Stock Upload: Emoji Picker — switch to "type the service name" mode ──
@dp.callback_query(F.data == "svcemoji_typemode")
async def cb_svcemoji_typemode(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    uid = c.from_user.id
    if uid not in state.admin_temp_data:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please re-upload.")
        return

    state.admin_pending_actions[uid] = "upload_stock_emoji_type"

    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_TOOL, '✏️')} <b>Type the Service Name (for Emoji)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} <i>Case matter kore na — lowercase/UPPERCASE "
        f"jekono bhabe likhle o match hobe (e.g. <code>facebook</code>, "
        f"<code>Netflix</code>, <code>tiktok</code>).</i>\n\n"
        f"Example: <code>Facebook</code>, <code>WhatsApp</code>, <code>Netflix</code>",
        markup([btn("↩️ Back to List", E_TOOL_BACKBUTTON, callback_data="svcemoji_pg_0", style="danger")],
               [btn("Close", E_BROADCAST_FAIL, callback_data="close_menu", style="danger")])
    )

# ── Stock Upload: Emoji Picker — pagination (Prev/Next) ──
@dp.callback_query(F.data.startswith("svcemoji_pg_"))
async def cb_svcemoji_page(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    uid = c.from_user.id
    if uid not in state.admin_temp_data:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please re-upload.")
        return

    page = int(c.data.split("_")[-1])
    await safe_edit(c.message, c.message.html_text or c.message.text, emoji_picker_keyboard(page))

# ── Stock Upload: Emoji Picker — service selected, bind emoji & finalize ──
@dp.callback_query(F.data.startswith("svcemoji_pick_"))
async def cb_svcemoji_pick(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    uid = c.from_user.id
    temp_data = state.admin_temp_data.pop(uid, None)
    if not temp_data:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please re-upload.")
        return

    idx = int(c.data.split("_")[-1])
    if idx < 0 or idx >= len(state.emoji_pick_options):
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Invalid selection! Please re-upload.")
        return

    emoji_key = state.emoji_pick_options[idx]
    await set_service_emoji(temp_data["service"], emoji_key)

    await safe_edit(
        c.message,
        f"{ce(E_FWD_OK, '✅')} <b>{svc_display_name(temp_data['service'])}</b> এখন থেকে "
        f"{_icon_for_emoji_key(emoji_key)} <b>{_MAIN_SVC_NAME.get(emoji_key, emoji_key.title())}</b> "
        f"emoji দিয়ে forward হবে।"
    )
    await _finalize_stock_upload(c.message, temp_data)

# ── Stock Upload: Emoji Picker — skip, keep default emoji ──
@dp.callback_query(F.data == "svcemoji_skip")
async def cb_svcemoji_skip(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    uid = c.from_user.id
    temp_data = state.admin_temp_data.pop(uid, None)
    if not temp_data:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please re-upload.")
        return

    await safe_edit(c.message, f"{ce(E_FWD_OK, '✅')} Default {ce(E_SVC_DEFAULT, '🔷')} emoji-e forward hobe.")
    await _finalize_stock_upload(c.message, temp_data)

# ================= BROADCAST =================



# ================= REPLY KEYBOARD HANDLERS =================

# Keep the most recent main-menu button message per user.
# When the user presses another button, delete ONLY the previous user message;
# the newly pressed button message remains visible.
PREVIOUS_REPLY_BUTTON_MESSAGES: dict[int, tuple[int, int]] = {}

async def _track_reply_button_message(m: types.Message):
    """Delete the previous user button message and previous service panel together.
    The currently pressed button message is always kept visible."""
    uid = m.from_user.id
    previous = PREVIOUS_REPLY_BUTTON_MESSAGES.get(uid)
    flow = state.service_flow_messages.get(uid)

    async def delete_user_message():
        if previous:
            old_chat_id, old_message_id = previous
            if old_message_id != m.message_id:
                try:
                    await bot.delete_message(old_chat_id, old_message_id)
                except Exception:
                    pass

    async def delete_service_message():
        if flow:
            old_chat_id = flow.get("chat_id")
            old_message_id = flow.get("message_id")
            if old_message_id and old_message_id != m.message_id:
                try:
                    await bot.delete_message(old_chat_id, old_message_id)
                except Exception:
                    pass

    # Both old elements vanish concurrently, keeping the visual timing in sync.
    await asyncio.gather(delete_user_message(), delete_service_message())
    state.service_flow_messages.pop(uid, None)
    PREVIOUS_REPLY_BUTTON_MESSAGES[uid] = (m.chat.id, m.message_id)


@dp.message(F.text.in_({"Get Number", "📱 Get Number", "📞 Get Number"}), F.chat.type == "private")
async def rk_get_number(m: types.Message):
    uid = m.from_user.id
    if uid not in state.verified_users:
        return

    # Remove the previous button message, but keep this newly pressed button message.
    await _track_reply_button_message(m)

    # A new Get Number session replaces the previous service/number panel.
    await _clear_previous_service_flow(uid)
    await _release_other_stock_services(m.chat.id)
    for phone, info in list(state.active_orders.items()):
        if info.get("chat_id") == m.chat.id:
            state.active_orders.pop(phone, None)

    kb = await _stock_menu_inline_kb()
    if not kb:
        await m.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} No stock available right now.",
            reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu", style="danger")])
        )
        return

    sent = await m.answer(
        "📍 <b>Select a service from the list</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    _remember_service_flow(uid, sent, "menu")

# ================= REFERENCE MENU EXTRAS =================

@dp.message(F.text.in_({"Live Traffic", "📡 Live Traffic"}), F.chat.type == "private")
async def rk_live_traffic(m: types.Message):
    uid = m.from_user.id
    if uid not in state.verified_users:
        return

    active = state.active_orders or {}
    if not active:
        text = (
            "📡 <b>Live Traffic</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🟢 Status: <b>Online</b>\n"
            "📭 No active numbers right now."
        )
    else:
        lines = [
            "📡 <b>Live Traffic</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"🟢 Active numbers: <b>{len(active)}</b>",
        ]
        for number, order in list(active.items())[:20]:
            svc = html.escape(str(order.get("svc", "Unknown")))
            seen = len(order.get("seen_otps", set()) or [])
            lines.append(f"📱 <code>+{html.escape(str(number))}</code> · {svc} · OTPs: <b>{seen}</b>")
        text = "\n".join(lines)

    await m.answer(text, parse_mode="HTML", reply_markup=markup(
        [btn("🔄 Refresh", E_TOOL_REFRESHING, callback_data="live_traffic_refresh", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu", style="danger")],
    ))

@dp.callback_query(F.data == "live_traffic_refresh")
async def cb_live_traffic_refresh(c: types.CallbackQuery):
    await c.answer("Updated")
    active = state.active_orders or {}
    if not active:
        text = "📡 <b>Live Traffic</b>\n━━━━━━━━━━━━━━━━━━━━\n\n🟢 Status: <b>Online</b>\n📭 No active numbers right now."
    else:
        lines = ["📡 <b>Live Traffic</b>", "━━━━━━━━━━━━━━━━━━━━", "", f"🟢 Active numbers: <b>{len(active)}</b>"]
        for number, order in list(active.items())[:20]:
            svc = html.escape(str(order.get("svc", "Unknown")))
            seen = len(order.get("seen_otps", set()) or [])
            lines.append(f"📱 <code>+{html.escape(str(number))}</code> · {svc} · OTPs: <b>{seen}</b>")
        text = "\n".join(lines)
    await safe_edit(c.message, text, markup(
        [btn("🔄 Refresh", E_TOOL_REFRESHING, callback_data="live_traffic_refresh", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu", style="danger")],
    ))

@dp.message(F.text.in_({"2F Auth", "🔐 2F Auth"}), F.chat.type == "private")
async def rk_2fa(m: types.Message):
    uid = m.from_user.id
    if uid not in state.verified_users:
        return
    await m.answer(
        "🔐 <b>2F Auth</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Two-factor authentication settings are not configured in this bot yet.",
        parse_mode="HTML",
        reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu", style="danger")])
    )

@dp.message(F.text.in_({"Admin Panel", "⚙️ Admin Panel"}), F.chat.type == "private")
async def rk_admin_panel(m: types.Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    panel_msg = await m.answer(await admin_stats_text(), reply_markup=admin_keyboard_for(uid))
    await _track_admin_flow_message(uid, "admin_panel_id", panel_msg)

# ================= SUPPORT =================

@dp.message(F.text.in_({"Support", "💬 Support"}), F.chat.type == "private")
async def rk_support(m: types.Message):
    uid = m.from_user.id
    if uid not in state.verified_users:
        return
    # Remove the previous button message, but keep this newly pressed button message.
    await _track_reply_button_message(m)
    # Switching to Support removes any active Get Number/Profile screen.
    await _clear_previous_service_flow(uid)
    await _release_other_stock_services(m.chat.id)
    for phone, info in list(state.active_orders.items()):
        if info.get("chat_id") == m.chat.id:
            state.active_orders.pop(phone, None)

    username = get_support_username()
    if not username:
        return
    # Minimal support UI: only the requested text and one compact green button.
    sent = await m.answer(
        "💬 <b>Contact Support</b> 👇",
        reply_markup=markup([
            btn("💬 Contact Support", E_BROADCAST, url=f"https://t.me/{username}", style="danger")
        ]),
        parse_mode="HTML",
    )
    _remember_service_flow(uid, sent, "support")

# ================= BALANCE & WITHDRAWAL HANDLERS =================

@dp.callback_query(F.data == "user_withdraw")
async def cb_user_withdraw(c: types.CallbackQuery):
    uid = c.from_user.id
    if not is_withdraw_enabled():
        await c.answer("⚠️ Withdraw Disabled Right now", show_alert=True)
        return
    bal = await get_balance(uid)
    min_wd = get_min_withdraw()
    if bal < min_wd:
        await c.answer(f"⚠️ Minimum withdrawal is {min_wd:.2f} TK!", show_alert=True)
        return

    state.pending_withdrawal_amount.pop(uid, None)
    state.pending_withdrawal_details.pop(uid, None)
    state.pending_withdrawal_method[uid] = True

    otp_stats = await get_user_otp_stats(uid)
    total_otp = sum(int(item.get("count", 0)) for item in otp_stats.values())
    daily_otp = await get_today_otp_count(uid)
    text = (
        "《 😔 WITHDRAWAL 》\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 <b>TOTAL OTP:</b> <code>{total_otp}</code>\n"
        f"📅 <b>TODAY OTP:</b> <code>{daily_otp}</code>\n"
        f"💰 <b>BALANCE:</b> <code>{bal:.2f} TK</code>\n"
        f"🔐 <b>MINIMUM WITHDRAW:</b> <code>{min_wd:.2f} TK</code>\n\n"
        "<b>SELECT PAYMENT METHOD:</b>"
    )
    kb = markup(
        [btn("NAGAD", callback_data="withdraw_opt_nagad", style="danger"),
         btn("BKASH", E_WD_BKASH, callback_data="withdraw_opt_bkash", style="danger")],
        [btn("BINANCE UID", E_WD_BINANCE, callback_data="withdraw_opt_binance", style="danger")],
        [btn("↩️ Back", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")]
    )
    await safe_edit(c.message, text, kb)

@dp.callback_query(F.data == "back_to_balance")
async def cb_back_to_balance(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    state.pending_withdrawal_amount.pop(uid, None)
    state.pending_withdrawal_method.pop(uid, None)
    state.pending_withdrawal_details.pop(uid, None)
    bal = await get_balance(uid)
    text = (
        f"💳 <code>{bal:.2f} TK</code>   💵 min <code>{get_min_withdraw():.2f} TK</code>"
    )
    kb = markup(
        [btn("Withdraw Balance", E_ADMIN_BOLT, callback_data="user_withdraw", style="danger")]
    )
    await safe_edit(c.message, text, kb)

@dp.callback_query(F.data.startswith("withdraw_opt_"))
async def cb_withdraw_opt(c: types.CallbackQuery):
    if not is_withdraw_enabled():
        await c.answer("⚠️ Withdraw Disabled Right now", show_alert=True)
        return
    uid = c.from_user.id
    if uid not in state.pending_withdrawal_method:
        await c.answer("⚠️ Session expired! Please start withdrawal again.", show_alert=True)
        return

    method = c.data.split("_")[-1]
    method_titles = {
        "binance": "Binance UID",
        "nagad": "Nagad Number",
        "bkash": "Bkash Number",
    }
    method_title = method_titles.get(method, method.title())

    state.pending_withdrawal_method.pop(uid, None)
    # Remember where the withdrawal was opened from so Back returns to the
    # correct previous screen (Profile or Balance).
    back_callback = "profile_back" if getattr(c.message, "reply_markup", None) and any(
        getattr(button, "callback_data", None) == "profile_back"
        for row in (getattr(c.message.reply_markup, "inline_keyboard", []) or [])
        for button in row
    ) else "back_to_balance"
    state.pending_withdrawal_details[uid] = {"method": method_title, "back_callback": back_callback}

    try:
        await c.answer()
    except Exception:
        pass

    if method == "binance":
        prompt = "📤 Send your Binance UID 👇"
    elif method == "nagad":
        prompt = "📤 Send your Nagad Number 👇"
    else:
        prompt = "📤 Send your Bkash Number 👇"

    await safe_edit(
        c.message,
        prompt,
        markup([btn("↩️ Back", E_BROADCAST_FAIL, callback_data=back_callback, style="danger")])
    )

# ── Admin payment decision callbacks ──
@dp.callback_query(F.data.startswith("adm_pay_"))
async def cb_admin_pay_action(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return

    parts = c.data.split("_")
    action = parts[2] # "appr" or "decl"
    w_id = int(parts[3])
    
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id, amount, method, details, status, requested_usd FROM withdrawals WHERE id = ?", (w_id,)) as cursor:
            row = await cursor.fetchone()
            
    if not row:
        await c.answer("❌ Withdrawal request not found!", show_alert=True)
        return
        
    user_id, amount, method, details, status, requested_usd = row
    
    if status != "pending":
        await c.answer(f"⚠️ This request is already {status}!", show_alert=True)
        return
        
    if action == "appr":
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (w_id,))
            await db.commit()
            
        await c.answer("Approved successfully!", show_alert=True)
        await safe_edit(
            c.message,
            c.message.text + f"\n\n✅ <b>Approved & Paid!</b>"
        )
        
        try:
            paid_amount_text = (f"${requested_usd:.2f} USD" if str(method).lower().startswith("binance") and requested_usd is not None else f"{amount:.2f} TK")
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 <b>Withdrawal Approved!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"💰 <b>Amount:</b> <code>{paid_amount_text}</code>\n"
                    f"🏦 <b>Method:</b> <code>{method}</code>\n"
                    f"📝 <b>Details:</b> <code>{details}</code>\n\n"
                    f"✅ Payment has been sent by the admin. Thank you!"
                )
            )
        except Exception:
            pass

    elif action == "decl":
        # Refund balance
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE withdrawals SET status = 'declined' WHERE id = ?", (w_id,))
            await db.execute(
                "INSERT INTO balances (user_id, balance) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?",
                (user_id, amount, amount)
            )
            await db.commit()
            
        await c.answer("Declined and refunded!", show_alert=True)
        await safe_edit(
            c.message,
            c.message.text + f"\n\n❌ <b>Declined & Refunded!</b>"
        )
        
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"❌ <b>Withdrawal Declined!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"💰 <b>Refunded:</b> <code>{amount:.2f} TK</code> to your bot balance.\n"
                    + (f"💵 <b>Requested:</b> <code>${requested_usd:.2f} USD</code>\n" if str(method).lower().startswith("binance") and requested_usd is not None else "")
                    + f"🏦 <b>Method:</b> <code>{method}</code>\n\n"
                    f"⚠️ Please verify your details and try again."
                )
            )
        except Exception:
            pass

@dp.callback_query(F.data.startswith("hadi_ctry_"))
async def cb_hadi_ctry(c: types.CallbackQuery):
    """Lease stock for the exact country selected by the user.

    Country names in old stock rows were sometimes normalized differently
    from the country label used by the keyboard (case/spacing/formatting).
    Resolve the callback against the actual available stock before allocating,
    instead of relying on one exact string match.
    """
    try:
        try:
            await c.answer("Leasing numbers...")
        except Exception:
            pass


        payload = str(c.data or "")[len("hadi_ctry_"):]
        if "_" not in payload:
            return

        chat_id = c.message.chat.id

        async with aiosqlite.connect(DB_FILE, timeout=30) as db:
            await db.execute("PRAGMA busy_timeout=30000")

            # Resolve the selected service/country before taking the write lock.
            # The previous code held BEGIN IMMEDIATE while scanning stock/history,
            # making other requests wait and making number loading feel slow.
            # Find the service/country pair from the real stock list.  This also
            # handles service names containing underscores.
            async with db.execute(
                """
                SELECT DISTINCT n.service, n.country
                FROM numbers n
                WHERE n.status = 'available'
                  AND TRIM(COALESCE(n.service, '')) != ''
                  AND TRIM(COALESCE(n.country, '')) != ''
                  AND NOT EXISTS (
                      SELECT 1 FROM number_usage_history h
                      WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
                          = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(n.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
                  )
                """
            ) as cur:
                available_pairs = await cur.fetchall()

            payload_norm = payload.strip().casefold()
            candidates = []
            for svc, country in available_pairs:
                svc_s = str(svc or "").strip()
                country_s = str(country or "").strip()
                encoded = f"{svc_s}_{country_s.replace(' ', '_')}"
                if encoded.casefold() == payload_norm:
                    candidates.append((svc_s, country_s))

            # Fallback for country-name formatting differences.
            if not candidates:
                def _norm_country(v):
                    return re.sub(r"[^a-z0-9]+", "", str(v or "").casefold())

                for svc, country in available_pairs:
                    svc_s = str(svc or "").strip()
                    country_s = str(country or "").strip()
                    prefix = svc_s + "_"
                    if payload_norm.startswith(prefix.casefold()):
                        cb_country = payload[len(prefix):].replace("_", " ").strip()
                        if _norm_country(country_s) == _norm_country(cb_country):
                            candidates.append((svc_s, country_s))

            if not candidates:
                # Last fallback: longest matching service prefix, then compare the
                # normalized country portion. This avoids breaking service names
                # that themselves contain underscores.
                services = sorted(
                    {str(s or "").strip() for s, _ in available_pairs if str(s or "").strip()},
                    key=len, reverse=True
                )
                for svc_s in services:
                    prefix = svc_s + "_"
                    if payload_norm.startswith(prefix.casefold()):
                        cb_country = payload[len(prefix):].replace("_", " ").strip()
                        for svc, country in available_pairs:
                            if str(svc or "").strip().casefold() == svc_s.casefold() and _norm_country(country) == _norm_country(cb_country):
                                candidates.append((svc_s, str(country).strip()))
                        if candidates:
                            break

            if not candidates:
                await db.rollback()
                await safe_edit(
                    c.message,
                    f"{ce(E_BROADCAST_FAIL, '❌')} <b>No numbers available for the selected country.</b>",
                    markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu_stock_home", style="danger")])
                )
                return

            service_name, country_name = candidates[0]

            # Acquire the write lock only after the lookup work is complete.
            # This shortens lock contention while keeping allocation atomic.
            await db.execute("BEGIN IMMEDIATE")

            # A user may have an active lease from the previously selected country.
            # Retire any lease that does not match the newly selected service/country;
            # otherwise the generic "busy number" lookup can keep showing the old country.
            await db.execute(
                "UPDATE numbers SET status = 'retired', assigned_to = NULL, assign_time = NULL "
                "WHERE assigned_to = ? AND status = 'busy' "
                "AND (LOWER(TRIM(COALESCE(service, ''))) != LOWER(TRIM(?)) "
                "OR LOWER(TRIM(REPLACE(COALESCE(country, ''), '_', ' '))) != "
                "LOWER(TRIM(REPLACE(?, '_', ' '))))",
                (chat_id, service_name, country_name),
            )

            # Only reuse an active lease when it belongs to the exact selected pair.
            async with db.execute(
                "SELECT phone_number FROM numbers "
                "WHERE assigned_to = ? AND service = ? AND country = ? AND status = 'busy' LIMIT 1",
                (chat_id, service_name, country_name)
            ) as cur:
                existing = await cur.fetchone()

            if existing:
                async with db.execute(
                    "SELECT phone_number, otp_rate FROM numbers "
                    "WHERE assigned_to = ? AND service = ? AND country = ? AND status = 'busy'",
                    (chat_id, service_name, country_name)
                ) as cur2:
                    busy_rows = await cur2.fetchall()
                    phone_nums = [r[0] for r in busy_rows]
                    selected_rate = {r[0]: r[1] for r in busy_rows if r[1] is not None}
            else:
                async with db.execute(
                    """
                    SELECT phone_number, otp_rate FROM numbers
                    WHERE LOWER(TRIM(COALESCE(service, ''))) = LOWER(TRIM(?))
                      AND LOWER(TRIM(REPLACE(COALESCE(country, ''), '_', ' '))) = LOWER(TRIM(REPLACE(?, '_', ' ')))
                      AND status = 'available'
                      AND NOT EXISTS (
                          SELECT 1 FROM number_usage_history h
                          WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
                              = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '')
                      )
                    ORDER BY RANDOM() LIMIT 3
                    """,
                    (service_name, country_name)
                ) as cursor:
                    rows = await cursor.fetchall()

                if not rows:
                    await db.rollback()
                    await safe_edit(
                        c.message,
                        f"{ce(E_BROADCAST_FAIL, '❌')} <b>No unused numbers are available for {html.escape(country_name)}.</b>\n\nPlease choose another country or upload fresh stock.",
                        markup([btn("🧭 Change Country", callback_data=f"hadi_chgctry_{service_name}", style="danger"), btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data=f"hadi_svc_{service_name}", style="danger")])
                    )
                    return

                phone_nums = [r[0] for r in rows]
                selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
                curr_time = int(time.time())
                allocated = []
                for pn in phone_nums:
                    cur = await db.execute(
                        "UPDATE numbers SET status = 'busy', assigned_to = ?, assign_time = ?, otp_received = 0, sms_seen_count = 0 "
                        "WHERE phone_number = ? AND status = 'available' "
                        "AND NOT EXISTS (SELECT 1 FROM number_usage_history h WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''))",
                        (chat_id, curr_time, pn)
                    )
                    if cur.rowcount:
                        allocated.append(pn)
                        await db.execute(
                            "INSERT OR IGNORE INTO number_usage_history (phone_number, first_used_at) VALUES (?, ?)",
                            (pn, curr_time)
                        )

                if not allocated:
                    await db.rollback()
                    await safe_edit(
                        c.message,
                        f"{ce(E_BROADCAST_FAIL, '❌')} <b>Stock was just taken. Please select the country again.</b>",
                        markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data=f"hadi_svc_{service_name}", style="danger")])
                    )
                    return
                phone_nums = allocated
                await db.commit()

        display_country = get_country(phone_nums[0]) or country_name
        label = svc_tag(service_name)
        text, kb = build_hadi_number_card(
            service_name, country_name, phone_nums, display_country, label,
            otp_rate=selected_rate, user_id=c.from_user.id
        )
        await safe_edit(c.message, text, kb)

    except Exception as e:
        logging.exception("[CountrySelect] Failed while leasing numbers")
        try:
            await safe_edit(
                c.message,
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Could not load numbers right now.</b>\nPlease try again in a few seconds.",
                markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu_stock_home", style="danger")])
            )
        except Exception:
            pass
        try:
            await c.answer("Database busy; please try again.", show_alert=True)
        except Exception:
            pass

async def _toggle_hadi_cc(c: types.CallbackQuery, cc_removed: bool):

    chat_id = c.message.chat.id
    rest = c.data.split("_", 2)[2]
    svc_end = rest.index("_")
    service_name = rest[:svc_end]
    country_name = rest[svc_end + 1:].replace("_", " ")

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT phone_number, otp_rate FROM numbers WHERE assigned_to = ? AND service = ? AND status = 'busy'",
            (chat_id, service_name)
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await c.answer("⚠️ No active number found. It may have expired.", show_alert=True)
        return

    phone_nums = [r[0] for r in rows]
    selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
    display_country = get_country(phone_nums[0])
    label = svc_tag(service_name)
    text, kb = build_hadi_number_card(
        service_name, country_name, phone_nums, display_country, label,
        cc_removed=cc_removed, otp_rate=selected_rate, user_id=c.from_user.id
    )
    try:
        await safe_edit(c.message, text, kb)
    except Exception as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            raise


# Per-user Change Number cooldown/lock.
# A user can change numbers only once every 5 seconds, and duplicate
# taps while a change is running are ignored safely.
NUMBER_CHANGE_LAST: dict[int, float] = {}
NUMBER_CHANGE_LOCKS: dict[int, asyncio.Lock] = {}








# ── Backup users.json (users.json) ──

# ── Restore users.json / User Data ──

# ── Stats submenu ──


# ── Co-Admin management (super admin only — co-admins cannot manage admins) ──



# ── Broadcast submenu ──

# ── Users submenu ──


# ── Add / Deduct Balance ──




# ── Bot Settings submenu ──






@dp.callback_query(F.data == "adm_rates")
async def adm_rates_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    # Keep the Rate screen clean: no rate summary, minimum amount, or
    # explanatory text is printed above the service buttons. Rates are shown
    # directly on the service buttons below.
    text = (
        f"{ce(E_ADMIN_CASH, '💰')} <b>Rate</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ Profile display rate: <b>৳{get_usd_to_bdt_rate():g}</b>"
    )
    await safe_edit(c.message, text, await admin_rates_keyboard())









# ── Panel Management: list all panels (2 per row) ──

# ── Panel Management: single panel detail (Token/URL/Interval/Remove) ──


# ── Add Panel wizard: Panel Name ➜ API Token ➜ API URL ──
@dp.callback_query(F.data == "adm_add_panel")
async def cb_adm_add_panel(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    state.admin_temp_data[c.from_user.id] = {}
    state.admin_pending_actions[c.from_user.id] = "addpanel_name"

    await safe_edit(
        c.message,
        f"{ce(E_FWD_OK, '➕')} <b>Add New Panel</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} Panel-টার একটা নাম পাঠান (যেমন: <code>ZyronSMS</code>, <code>StexSMS</code>, <code>MNIT Network</code>)\n\n"
        f"<i>Step 1/3 — Name</i>",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="danger")])
    )

# ── Remove a dynamically-added panel (base 3 panels can't be removed) ──

# ── Force Join: category list ──
@dp.callback_query(F.data == "adm_forcejoin_mgmt")
async def cb_adm_forcejoin_mgmt(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    enabled = force_join_enabled()
    # Keep this screen limited to the requested title and divider only.
    await safe_edit(
        c.message,
        "🔗 <b>Force Join</b>\n━━━━━━━━━━━━━━━━",
        force_join_mgmt_keyboard(),
    )

# ── Force Join: master on/off toggle ──

# ── Force Join: single channel detail (URL/Remove) ──

# ── Force Join: start Add Channel wizard ──

# ── Force Join: remove a channel (base 2 can't be removed) ──

# ── Force Join: edit a channel's URL ──

# ── Auto Captcha Panel: category list ──

# ── Auto Captcha Panel: single panel detail ──

# ── Auto Captcha Panel: manual retry login ──

# ── Auto Captcha Panel: test connection (login if needed, fetch + parse,
#    show sample OTPs, or dump the full raw table to a .txt file so the
#    admin can read off the correct column serial numbers) ──

# ── Auto Captcha Panel: remove ──

# ── Add Auto Captcha Panel wizard: Name ➜ Login URL ➜ Username ➜ Password ➜
#    Msg Link ➜ Num Col Name ➜ Num Col Idx ➜ Msg Col Name ➜ Msg Col Idx ──

# ══════════════════════════════════════════════════════════════
#  GREEN PANEL (Green SMS) — আলাদা section, শুধু Name → Login URL →
#  Username → Password (কোনো column config লাগে না, JSON API auto-parse করে)
# ══════════════════════════════════════════════════════════════






# ── Add Green Panel wizard: Name ➜ Login URL ➜ Username ➜ Password ──

# ── SMS Hadi Upload Stock ──

# ── Admin Forward Pool wizard ──





# ── Delete a single Number Stock entry ──

# ── Direct Stock List / Delete Stock ──
DELETE_STOCK_PAGE_SIZE = 25





# ── Download Unused Stock ──




# ── Multi-Provider Settings Inputs (base panels + any dynamically added ones) ──
# NOTE: regex টা generic রাখা হয়েছে ("|".join(SMS_PROVIDERS) দিয়ে নয়), কারণ
# @dp.callback_query decorator শুধু একবার, bot import হওয়ার সময় রেজিস্টার হয় —
# তাই runtime এ নতুন panel add হলে সেই compile-time regex আর আপডেট হতো না।
# তার বদলে handler এর ভেতরে runtime এ SMS_PROVIDERS list check করা হচ্ছে।

# ── Shared helper: insert/update stock numbers with given service ──

# ── Shared helper: after OTP rate is set (or skipped), persist the numbers,
#    confirm to the admin, and broadcast — used by both the button-selected
#    and custom-typed service paths so the flow is identical either way. ──

# ── Shared helper: broadcast "new stock added" message to known users ──
async def _broadcast_new_stock(service_name, country_name, added_count, rate: float = None):
    """Send channel and user stock alerts independently."""
    if rate is None:
        rate = get_stock_rate(service_name)

    flag = country_flag_emoji(country_name)
    price_line = (
        f"{ce(E_ADMIN_CASH, '💰')} <b>OTP Price: {format_dual_rate(float(rate))}</b>"
        if rate is not None and float(rate) > 0 else ""
    )
    alert_text = (
        f"🆕 <b>New Stock Added</b> ✨\n\n"
        f"{flag} <b>{html.escape(country_name.upper())}</b> | "
        f"{svc_tag_icon(service_name)} <b>{html.escape(svc_display_name(service_name).upper())}</b>\n\n"
        f"{price_line}"
    )
    country_escaped = country_name.replace(" ", "_")
    channel_kb = markup([
        btn("GET NUMBER", E_RK_GET_NUM, url=FWD_GET_NUMBER_URL, style="__status_red__")
    ])
    bot_kb = markup([
        btn("GET NUMBER", E_RK_GET_NUM,
            callback_data=f"hadi_ctry_{service_name}_{country_escaped}",
            style="__status_red__")
    ])

    # Channel alert is controlled only by Channel Stock Alert.
    if CHANNEL_ID and is_channel_stock_alert_enabled():
        try:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=alert_text,
                parse_mode="HTML",
                reply_markup=channel_kb,
            )
            print(f"[StockChannel] Card sent: {service_name} / {country_name} / {added_count}")
        except Exception as err:
            print(f"[StockChannel] Send error: {err}")

    # Bot/user alert is controlled only by Bot Stock Alert.
    if not is_bot_stock_alert_enabled():
        return

    targets = list(state.known_users)
    if not targets:
        return

    sem = asyncio.Semaphore(20)

    async def _send_one(target_uid):
        async with sem:
            try:
                await bot.send_message(
                    target_uid,
                    alert_text,
                    parse_mode="HTML",
                    reply_markup=bot_kb,
                )
            except Exception:
                pass

    await asyncio.gather(*[_send_one(t) for t in targets])

# ── Stock Numbers allocation service callbacks ──


# ── Stock Numbers allocation: CUSTOM (manually typed) service name ──

# ── Stock Upload: Emoji Picker — switch to "type the service name" mode ──

# ── Stock Upload: Emoji Picker — pagination (Prev/Next) ──

# ── Stock Upload: Emoji Picker — service selected, bind emoji & finalize ──

# ── Stock Upload: Emoji Picker — skip, keep default emoji ──



# ================= BROADCAST =================
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_cb(c: types.CallbackQuery):
    try:
        await c.answer()
    except Exception:
        pass
    if not is_admin(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    state.pending_bc[c.from_user.id] = 'waiting'
    user_count = len(state.known_users)
    await safe_edit(
        c.message,
        f"{ce(E_BROADCAST, '📣')} <b>Broadcast Mode Active</b>\n\n"
        f"{ce(E_BROADCAST_USERS, '👥')} Total users: <b>{user_count}</b>\n\n"
        f"Send any message, photo, or video now.\n"
        f"Type /cancel to abort."
    )

@dp.message(Command("broadcast"))
async def broadcast_cmd(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    state.pending_bc[m.from_user.id] = 'waiting'
    await m.answer(
        f"{ce(E_BROADCAST, '📣')} <b>Broadcast Mode Active</b>\n\n"
        f"{ce(E_BROADCAST_USERS, '👥')} Total users: <b>{len(state.known_users)}</b>\n\n"
        f"Send any message, photo, or video now.\n"
        f"Type /cancel to abort."
    )

@dp.message(Command("cancel"))
async def cancel_cmd(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    uid = m.from_user.id
    state.pending_bc.pop(uid, None)
    state.admin_pending_actions.pop(uid, None)
    state.pending_withdrawal_details.pop(uid, None)
    await m.answer(f"{ce(E_BROADCAST_FAIL, '❌')} Action cancelled.", parse_mode="HTML")

# ================= REPLY KEYBOARD HANDLERS =================

# Keep the most recent main-menu button message per user.
# When the user presses another button, delete ONLY the previous user message;
# the newly pressed button message remains visible.
PREVIOUS_REPLY_BUTTON_MESSAGES: dict[int, tuple[int, int]] = {}




# ================= REFERENCE MENU EXTRAS =================





# ================= SUPPORT =================


# ================= BALANCE & WITHDRAWAL HANDLERS =================




# ── Admin payment decision callbacks ──

# ── Admin text input handler (ban/unban/viewstats tokens) ──
@dp.message(F.chat.type == "private")
async def prefix_input_priority(m: types.Message):
    uid = m.from_user.id
    if uid not in state.prefix_filter_pending or not m.text:
        raise SkipHandler()

    pending = state.prefix_filter_pending.pop(uid)
    raw_prefix = m.text.strip()

    # Delete the user's typed Prefix message. The original bot number-card
    # remains the single message that is edited throughout this flow.
    try:
        deleted = False
        try:
            await m.delete()
            deleted = True
        except Exception:
            deleted = False
        if not deleted:
            await _tg_client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                json={"chat_id": m.chat.id, "message_id": m.message_id},
                timeout=8.0,
            )
    except Exception as e:
        print(f"[Prefix] Could not delete input message: {e}")

    prefix = re.sub(r"\D", "", raw_prefix)

    # Prefix format:
    #   - country calling code: 1 to 4 digits
    #   - after the country code: 1 to 5 digits
    #
    # IMPORTANT: Do not depend on the displayed country/card text to discover
    # the calling code. Some number cards do not contain a parseable
    # "Country:" label, which previously made every Prefix input invalid.
    # Instead, resolve the calling code directly from the user's entered
    # digits using the actual COUNTRIES table (longest code first).
    selected_cc = ""
    selected_country = str(pending.get("country") or "").strip().lower()

    # First, if the pending flow has a known country, find its calling code.
    if selected_country and selected_country != "global":
        for cc, info in sorted(COUNTRIES.items(), key=lambda item: len(str(item[0])), reverse=True):
            country_label = str(info[0]).split("/")[0].strip().lower()
            if country_label == selected_country:
                selected_cc = str(cc)
                break

    # If the country is unavailable/unparseable, or the entered prefix does not
    # start with that code, resolve the code directly from the entered digits.
    # This supports 1–4 digit calling codes reliably.
    if not selected_cc or not prefix.startswith(selected_cc):
        selected_cc = ""
        for cc in sorted((str(k) for k in COUNTRIES.keys()), key=len, reverse=True):
            if 1 <= len(cc) <= 4 and prefix.startswith(cc):
                selected_cc = cc
                break

    suffix_len = len(prefix) - len(selected_cc) if selected_cc else -1
    valid_format = (
        bool(selected_cc)
        and 1 <= len(selected_cc) <= 4
        and prefix.startswith(selected_cc)
        and suffix_len in (1, 2, 3, 4, 5)
    )

    if not valid_format:
        state.prefix_filter_pending[uid] = pending
        example = f"{selected_cc}17123" if 1 <= len(selected_cc) <= 4 else "88017123"
        await m.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid Prefix.</b>\n\n"
            f"Max: 5 digits after Cuntry code \n"
            f"Example: <code>{example}</code>"
        )
        return

    service_name = pending["service"]
    message_id = int(pending["message_id"])
    chat_id = int(pending["chat_id"])

    # Resolve the country from the calling code when possible.
    country_name = str(pending.get("country") or "").strip()
    if not country_name or country_name.lower() == "global":
        for cc, info in sorted(COUNTRIES.items(), key=lambda item: len(str(item[0])), reverse=True):
            if prefix.startswith(str(cc)):
                country_name = str(info[0]).split("/")[0].strip()
                break

    # Allocate the first Prefix batch atomically. The old active numbers for
    # this service are retired only after matching stock has been found, so a
    # failed Prefix search never destroys the user's current numbers.
    async with aiosqlite.connect(DB_FILE, timeout=3.0) as db:
        await db.execute("PRAGMA busy_timeout = 2500")
        try:
            await db.execute("BEGIN IMMEDIATE")
        except Exception as e:
            print(f"[Prefix] DB lock: {e}")
            state.prefix_filter_pending[uid] = pending
            await safe_edit_by_ids(
                chat_id,
                message_id,
                f"{ce(E_BROADCAST_FAIL, '⚠️')} <b>Prefix filter is temporarily busy.</b>\n\nPlease try again in a moment.",
                markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="hadi_prefix_back", style="danger")]),
            )
            return

        if country_name:
            query = (
                "SELECT phone_number, otp_rate FROM numbers "
                "WHERE service = ? AND country = ? AND status = 'available' "
                "AND REPLACE(REPLACE(REPLACE(phone_number, '+', ''), ' ', ''), '-', '') LIKE ? "
                "AND NOT EXISTS (SELECT 1 FROM number_usage_history h "
                "                WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')) "
                "ORDER BY RANDOM() LIMIT 3"
            )
            params = (service_name, country_name, prefix + "%")
        else:
            query = (
                "SELECT phone_number, otp_rate FROM numbers "
                "WHERE service = ? AND status = 'available' "
                "AND REPLACE(REPLACE(REPLACE(phone_number, '+', ''), ' ', ''), '-', '') LIKE ? "
                "AND NOT EXISTS (SELECT 1 FROM number_usage_history h "
                "                WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', '')) "
                "ORDER BY RANDOM() LIMIT 3"
            )
            params = (service_name, prefix + "%")

        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

        if not rows:
            await db.rollback()
            state.prefix_filter_pending[uid] = pending
            await safe_edit_by_ids(
                chat_id,
                message_id,
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>No numbers found.</b>\n\n"
                f"Service: <b>{html.escape(svc_display_name(service_name))}</b>\n"
                f"Prefix: <code>{html.escape(prefix)}</code>\n\n"
                f"Try another prefix.",
                markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="hadi_prefix_back", style="danger")]),
            )
            return

        phone_nums = [r[0] for r in rows]
        selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
        curr_time = int(time.time())

        # Replace the user's currently leased numbers for this service.
        await db.execute(
            "UPDATE numbers SET status = 'retired', assigned_to = NULL, assign_time = NULL "
            "WHERE assigned_to = ? AND service = ? AND status = 'busy'",
            (chat_id, service_name),
        )

        allocated = []
        for pn in phone_nums:
            cur = await db.execute(
                "UPDATE numbers SET status = 'busy', assigned_to = ?, assign_time = ?, "
                "otp_received = 0, sms_seen_count = 0 "
                "WHERE phone_number = ? AND status = 'available' "
                "AND NOT EXISTS (SELECT 1 FROM number_usage_history h "
                "                WHERE REPLACE(REPLACE(REPLACE(REPLACE(h.phone_number, '+', ''), ' ', ''), '-', ''), '(', '') = REPLACE(REPLACE(REPLACE(REPLACE(numbers.phone_number, '+', ''), ' ', ''), '-', ''), '(', ''))",
                (chat_id, curr_time, pn),
            )
            if cur.rowcount:
                allocated.append(pn)
                await db.execute(
                    "INSERT OR IGNORE INTO number_usage_history (phone_number, first_used_at) "
                    "VALUES (?, ?)",
                    (pn, curr_time),
                )

        if not allocated:
            await db.rollback()
            state.prefix_filter_pending[uid] = pending
            await safe_edit_by_ids(
                chat_id,
                message_id,
                f"{ce(E_BROADCAST_FAIL, '⚠️')} <b>Stock was just taken.</b>\n\nPlease try the Prefix again.",
                markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="hadi_prefix_back", style="danger")]),
            )
            return

        phone_nums = allocated
        selected_rate = {r[0]: r[1] for r in rows if r[0] in allocated and r[1] is not None}
        await db.commit()

    # Save the accepted prefix only after successful allocation.
    state.prefix_saved[uid] = {
        "prefix": prefix,
        "service": service_name,
        "country": country_name,
    }
    state.prefix_filter_active[uid] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "service": service_name,
        "country": country_name,
        "prefix": prefix,
        "offset": len(phone_nums),
    }

    # Short animation on the ORIGINAL bot card (not the user's input message).
    try:
        await safe_edit_by_ids(chat_id, message_id, "🔄 <b>Changing Prefix...</b>")
        await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[Prefix] Change animation failed: {e}")

    display_country = country_name or country_name_for_number(phone_nums[0])
    text_card, kb = build_hadi_number_card(
        service_name,
        country_name,
        phone_nums[:3],
        display_country,
        svc_tag(service_name),
        otp_rate=selected_rate,
        user_id=uid,
    )

    try:
        await safe_edit_by_ids(chat_id, message_id, text_card, kb)
    except Exception as e:
        print(f"[Prefix] Failed to update number card: {e}")
    return


# Dedicated handler registered before generic/numeric message handlers so referral
# settings input cannot be consumed by another admin flow.
@dp.message(F.chat.type == "private")
async def handle_referral_setting_input(m: types.Message):
    uid = m.from_user.id
    action = state.admin_pending_actions.get(uid)
    if not (is_admin(uid) and action in {"set_referral_commission", "set_referral_bonus"} and m.text):
        raise SkipHandler()

    raw_val = m.text.strip().replace(",", ".").replace("%", "")
    try:
        new_value = float(raw_val)
        if new_value < 0 or (action == "set_referral_commission" and new_value > 100):
            raise ValueError
    except ValueError:
        expected = "0 থেকে 100-এর মধ্যে শতাংশ (যেমন 5)" if action == "set_referral_commission" else "0 বা তার বেশি টাকা (যেমন 1.50)"
        await m.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} <b>ভুল মান!</b>\n\nসঠিক মান পাঠান: {expected}",
            reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_referral_settings", style="danger")])
        )
        return

    if action == "set_referral_commission":
        await set_referral_setting("referral_commission_rate", new_value / 100.0)
        label = f"Lifetime commission updated to <b>{new_value:.2f}%</b>."
    else:
        await set_referral_setting("referral_bonus_per_refer", new_value)
        label = f"Referral bonus updated to <b>{new_value:.2f} TK</b>."

    state.admin_pending_actions.pop(uid, None)
    await m.answer(
        f"{ce(E_FWD_OK, '✅')} {label}\n\nপরিবর্তনটি ডাটাবেজে সংরক্ষণ করা হয়েছে।",
        reply_markup=markup(
            [btn("🔗 Referral Settings", E_RK_REFER_EARN, callback_data="adm_referral_settings", style="danger")],
            [btn("↩️ Back to Rates", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")],
        )
    )


@dp.message(F.chat.type == "private", F.text.regexp(r"^\d{5,12}$"), F.from_user.id.in_(ADMIN_IDS))
async def admin_userid_input(m: types.Message):
    uid = m.from_user.id

    # যদি admin নিজেই withdraw ফ্লো তে থাকে (amount / method / payment-details
    # দিচ্ছেন এবং সেটা ৫-১২ ডিজিটের সংখ্যা — যেমন Nagad number বা numeric
    # Binance ID), তাহলে এই handler সেটা গিলে ফেলবে না। বরং SkipHandler
    # রেইজ করে withdrawal handler (handle_private_message) এর কাছে ছেড়ে দেবে।
    if (uid in state.pending_withdrawal_amount
            or uid in state.pending_withdrawal_method
            or uid in state.pending_withdrawal_details
            or uid in state.prefix_filter_pending
            ):
        raise SkipHandler()

    pending_action = state.admin_pending_actions.get(uid)
    if pending_action == "set_support_username":
        raise SkipHandler()
    if pending_action == "all_user_search":
        state.admin_pending_actions.pop(uid, None)
        await _delete_admin_flow_message(uid, "prompt_id")
        try: await m.delete()
        except Exception: pass
        target = int(m.text.strip())
        if str(target) not in users_db:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} User <code>{target}</code> was not found in the user database.",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_all_users", style="danger")]),
            )
            return
        total_otp = await _user_total_otp(target)
        balance = await get_balance(target)
        blocked = target in state.banned_users
        await m.answer(
            _user_detail_text(target, total_otp, balance),
            reply_markup=markup(
                [btn("UNBLOCK USER" if blocked else "BLOCK USER", E_FWD_OK if blocked else E_BROADCAST_FAIL,
                     callback_data=f"adm_user_toggle_block_{target}", style="__status_red__" if blocked else None)],
                [btn("↩️ ALL USER", E_TOOL_BACKBUTTON, callback_data="adm_all_users", style="danger")],
            ),
        )
        return

    if pending_action == "remove_balance_amount":
        raise SkipHandler()

    if pending_action in {"forward_pool_service", "forward_pool_interval", "demo_otp_interval"}:
        # These inputs belong to the Forward Pool wizard and are handled by
        # the generic private-message handler below, not this numeric-ID
        # handler.
        raise SkipHandler()

    if pending_action == "add_co_admin":
        state.admin_pending_actions.pop(uid, None)
        if uid != ADMIN_ID:
            raise SkipHandler()  # only the super admin can manage co-admins
        target = int(m.text.strip())
        if target == ADMIN_ID or target in state.co_admin_ids:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <code>{target}</code> is already an admin.",
                reply_markup=admin_co_admins_keyboard()
            )
            return
        if len(state.co_admin_ids) >= MAX_CO_ADMINS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Max {MAX_CO_ADMINS} co-admins already added!",
                reply_markup=admin_co_admins_keyboard()
            )
            return
        state.co_admin_ids.add(target)
        await save_co_admins(state.co_admin_ids)
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <code>{target}</code> added as co-admin!",
            reply_markup=admin_co_admins_keyboard()
        )
        try:
            await bot.send_message(
                chat_id=target,
                text=f"{ce(E_ADMIN_TOOL, '🛠')} <b>You've been made a co-admin of this bot!</b>\n\n"
                     f"Send /admin or tap Admin Panel to access it.",
            )
        except Exception:
            pass
        return

    if pending_action == "add_balance_uid":
        state.admin_pending_actions.pop(uid, None)
        target = int(m.text.strip())
        cur_bal = await get_balance(target)
        state.admin_temp_data[uid] = {"add_balance_target": target}
        state.admin_pending_actions[uid] = "add_balance_amount"
        await m.answer(
            f"{ce(E_ADMIN_CASH, '💰')} <b>Target User:</b> <code>{target}</code>\n"
            f"Current Balance: <b>{cur_bal:.2f} TK</b>\n\n"
            f"{ce(E_MENU_PIN, '📌')} Send the amount to <b>add</b> (e.g. <code>100</code>).\n"
            f"Send a negative number (e.g. <code>-50</code>) to <b>deduct</b>.",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
        )
        return

    if pending_action == "remove_balance_uid":
        state.admin_pending_actions.pop(uid, None)
        target = int(m.text.strip())
        cur_bal = await get_balance(target)
        state.admin_temp_data[uid] = {"remove_balance_target": target}
        state.admin_pending_actions[uid] = "remove_balance_amount"
        await m.answer(
            f"{ce(E_ADMIN_CASH, '💰')} <b>Target User:</b> <code>{target}</code>\n"
            f"Current Balance: <b>{cur_bal:.2f} TK</b>\n\n"
            f"{ce(E_MENU_PIN, '📌')} Send the amount to <b>remove</b> (e.g. <code>100</code>).",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
        )
        return

    if pending_action == "add_balance_amount":
        raise SkipHandler() # a large all-digit amount; parsed in generic fallback instead

    if pending_action and pending_action.startswith("set_"):
        raise SkipHandler() # parsed in generic fallback instead
        
    action = state.pending_user_search.pop(uid, None)
    if not action:
        return
    target = int(m.text.strip())
    if action == 'ban':
        state.banned_users.add(target)
        state.verified_users.discard(target)
        await m.answer(
            f"{ce(E_BROADCAST_FAIL, '❌')} User <code>{target}</code> has been <b>banned</b>.",
            reply_markup=admin_keyboard()
        )
    elif action == 'unban':
        state.banned_users.discard(target)
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} User <code>{target}</code> has been <b>unbanned</b>.",
            reply_markup=admin_keyboard()
        )


    # ── Remove Balance Amount handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "remove_balance_amount" and m.text:
        state.admin_pending_actions.pop(uid, None)
        temp_data = state.admin_temp_data.pop(uid, None)
        target = temp_data.get("remove_balance_target") if temp_data else None
        if not target:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please start again.",
                reply_markup=admin_keyboard()
            )
            return
        try:
            amount = float(m.text.strip().replace(",", "."))
            if amount <= 0:
                raise ValueError
        except ValueError:
            state.admin_temp_data[uid] = {"remove_balance_target": target}
            state.admin_pending_actions[uid] = "remove_balance_amount"
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid amount!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} Send a positive amount, e.g. <code>100</code>.",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
            )
            return

        current = await get_balance(target)
        if current < amount:
            state.admin_temp_data[uid] = {"remove_balance_target": target}
            state.admin_pending_actions[uid] = "remove_balance_amount"
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Insufficient balance.</b>\n"
                f"Current: <b>{current:.2f} TK</b>\n"
                f"Requested: <b>{amount:.2f} TK</b>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
            )
            return

        ok = await deduct_balance(target, amount)
        new_bal = await get_balance(target)
        if not ok:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Balance update failed.",
                reply_markup=admin_keyboard()
            )
            return
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>Balance Removed!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>User ID:</b> <code>{target}</code>\n"
            f"💰 <b>Removed:</b> <code>{amount:.2f} TK</code>\n"
            f"💵 <b>New Balance:</b> <code>{new_bal:.2f} TK</code>",
            reply_markup=admin_users_keyboard()
        )
        return

# ================= DOCUMENT FILE UPLOAD =================

async def _run_broadcast(m: types.Message):
    """Send the admin's current message to every registered user.

    Uses both the persistent users_db and the in-memory known_users set so
    legacy/just-restored users are not skipped. copy_to preserves the original
    message type, caption and Telegram entities for text/photo/video/document.
    """
    uid = m.from_user.id
    if not is_admin(uid) or state.pending_bc.get(uid) != 'waiting':
        return False

    state.pending_bc.pop(uid, None)
    # IMPORTANT: do not delete the source message before copy_to().
    # Telegram copyMessage/copy_to needs the original message_id to still exist.
    # The admin input is deleted only after all recipients have been processed.

    targets = set()
    targets.update(state.known_users)
    if isinstance(users_db, dict):
        for key in users_db.keys():
            try:
                targets.add(int(key))
            except (TypeError, ValueError):
                continue
    targets.discard(int(uid))
    targets = sorted(targets)

    if not targets:
        await m.answer(f"{ce(E_ADMIN_WRENCH, '⚠️')} No users found yet.", parse_mode="HTML")
        return True

    status_msg = await m.answer(
        f"{ce(E_BROADCAST_SEND, '📤')} Starting broadcast to <b>{len(targets)}</b> users...",
        parse_mode="HTML"
    )

    sem = asyncio.Semaphore(15)

    async def _send_one(target_uid):
        async with sem:
            for attempt in range(3):
                try:
                    await m.copy_to(target_uid)
                    return True
                except Exception as err:
                    err_text = str(err).lower()
                    # Telegram may temporarily rate-limit a batch. Wait briefly
                    # and retry that user instead of counting it as an immediate failure.
                    if "retry after" in err_text and attempt < 2:
                        match = re.search(r"retry after\s+(\d+)", err_text)
                        wait_s = min(int(match.group(1)) if match else 2, 10)
                        await asyncio.sleep(wait_s)
                        continue
                    return False
            return False

    results = await asyncio.gather(*[_send_one(t) for t in targets])
    sent = sum(1 for r in results if r is True)
    failed = len(targets) - sent

    try:
        await status_msg.edit_text(
            f"{ce(E_BROADCAST_DONE, '✅')} <b>Broadcast Complete!</b>\n\n"
            f"{ce(E_BROADCAST_SEND, '📨')} Sent  : <b>{sent}</b>\n"
            f"{ce(E_BROADCAST_FAIL, '❌')} Failed: <b>{failed}</b>\n"
            f"{ce(E_BROADCAST_USERS,'👥')} Total : <b>{len(targets)}</b>"
        )
    except Exception as err:
        print(f"[Broadcast] Status update failed: {err}")

    # Now that Telegram has finished copying the message, remove the source
    # message from the admin chat as a temporary-flow cleanup step.
    await _delete_admin_input_message(m)
    return True

@dp.message(F.chat.type == "private", F.document, F.from_user.id.in_(ADMIN_IDS))
async def handle_document_upload(m: types.Message):
    uid = m.from_user.id

    # Broadcast must take priority over document/stock-upload handling.
    # Otherwise an admin's broadcast file is intercepted by this handler.
    if await _run_broadcast(m):
        return

    action = state.admin_pending_actions.get(uid)

    # ── User balance backup import ──
    if action == "import_user_balances":
        state.admin_pending_actions.pop(uid, None)
        status_msg = await m.answer("⏳ Reading user balance backup...")
        try:
            filename = (m.document.file_name or "").lower()
            if not filename.endswith(".json"):
                raise ValueError("শুধু .json balance backup file গ্রহণ করা হবে।")

            file_info = await bot.get_file(m.document.file_id)
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
            try:
                resp = await _tg_client.get(
                    file_url,
                    timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
                )
                resp.raise_for_status()
                file_bytes = resp.content
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
                ) as fallback_client:
                    resp = await fallback_client.get(file_url)
                    resp.raise_for_status()
                    file_bytes = resp.content

            backup = json.loads(file_bytes.decode("utf-8-sig"))
            if not isinstance(backup, dict) or backup.get("format") != "telegram_bot_user_balances_v1":
                raise ValueError("এটি সঠিক User Balance Backup JSON নয়।")

            backup_id = str(backup.get("backup_id") or "").strip()
            items = backup.get("balances")
            if not backup_id or not isinstance(items, list) or not items:
                raise ValueError("Backup ID বা balance list পাওয়া যায়নি।")

            clean_rows = []
            seen_ids = set()
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Backup-এ ভুল row format আছে।")
                user_id = int(item.get("user_id"))
                amount = float(item.get("balance"))
                if user_id <= 0 or not (amount > 0) or not (amount < 1e15):
                    raise ValueError("Backup-এ invalid user ID বা balance amount আছে।")
                if user_id in seen_ids:
                    raise ValueError(f"Backup-এ duplicate User ID আছে: {user_id}")
                seen_ids.add(user_id)
                clean_rows.append((user_id, amount))

            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    "SELECT 1 FROM imported_balance_backups WHERE backup_id = ?",
                    (backup_id,),
                ) as cur:
                    already_imported = await cur.fetchone()
                if already_imported:
                    await db.rollback()
                    await status_msg.edit_text(
                        "⚠️ এই backup file আগেই import করা হয়েছে। Duplicate balance যোগ করা হয়নি।",
                        reply_markup=admin_users_keyboard(),
                    )
                    return

                await db.executemany(
                    """INSERT INTO balances (user_id, balance) VALUES (?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET balance = balances.balance + excluded.balance""",
                    clean_rows,
                )
                await db.execute(
                    """INSERT INTO imported_balance_backups
                       (backup_id, imported_at, imported_by, row_count)
                       VALUES (?, ?, ?, ?)""",
                    (backup_id, datetime.now(BD_TZ).isoformat(), int(uid), len(clean_rows)),
                )
                await db.commit()

            total_added = sum(amount for _, amount in clean_rows)
            await status_msg.edit_text(
                "✅ <b>User balances imported successfully!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👥 Updated users: <b>{len(clean_rows)}</b>\n"
                f"💰 Total added: <b>{total_added:.2f} TK</b>\n"
                "📌 প্রতিটি User ID-এর পুরোনো ব্যালেন্সের সঙ্গে backup-এর টাকা যোগ হয়েছে।",
                reply_markup=admin_users_keyboard(),
                parse_mode="HTML",
            )
        except Exception as err:
            await status_msg.edit_text(
                f"❌ Balance import failed: <code>{html.escape(str(err))}</code>",
                reply_markup=admin_users_keyboard(),
                parse_mode="HTML",
            )
        return

    # ── User data restore ──
    if action == "restore_users":
        state.admin_pending_actions.pop(uid, None)
        status_msg = await m.answer(f"{ce(E_NUM_WAITING, '⏳')} Reading user data backup...")
        try:
            file_info = await bot.get_file(m.document.file_id)
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
            try:
                resp = await _tg_client.get(file_url, timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0))
                resp.raise_for_status()
                file_bytes = resp.content
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError):
                async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)) as fallback_client:
                    resp = await fallback_client.get(file_url)
                    resp.raise_for_status()
                    file_bytes = resp.content

            filename = m.document.file_name or ""
            if os.path.splitext(filename)[1].lower() != ".json":
                await status_msg.edit_text(
                    f"{ce(E_BROADCAST_FAIL, '❌')} Please upload a <code>.json</code> user backup file.",
                    reply_markup=admin_keyboard()
                )
                return

            restored = json.loads(file_bytes.decode("utf-8-sig"))
            if not isinstance(restored, dict):
                raise ValueError("Invalid user backup format: root must be a JSON object")

            # Validate the basic user-record shape while allowing older backups
            # to contain additional fields.
            clean_users = {}
            for uid_str, info in restored.items():
                try:
                    uid_int = int(uid_str)
                except (TypeError, ValueError):
                    continue
                if not isinstance(info, dict):
                    continue
                clean_users[str(uid_int)] = info

            if not clean_users and restored:
                raise ValueError("No valid Telegram user records found in backup")

            users_db.clear()
            users_db.update(clean_users)

            # Rebuild the in-memory registered/verified user sets from the
            # restored database so the bot immediately uses the uploaded data.
            state.known_users.clear()
            state.verified_users.clear()
            for uid_str in users_db:
                try:
                    uid_int = int(uid_str)
                    state.known_users.add(uid_int)
                    state.verified_users.add(uid_int)
                except (TypeError, ValueError):
                    pass

            global _last_save_time
            _last_save_time = 0.0
            save_users(users_db)

            await status_msg.edit_text(
                f"{ce(E_FWD_OK, '✅')} <b>User data restored successfully.</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{ce(E_BROADCAST_USERS, '👥')} Restored users: <b>{len(users_db)}</b>\n"
                f"{ce(E_ADMIN_DATE, '📅')} Saved to: <code>{USERS_FILE}</code>",
                reply_markup=admin_users_keyboard()
            )
        except Exception as err:
            await status_msg.edit_text(
                f"{ce(E_BROADCAST_FAIL, '❌')} User data restore failed: <code>{html.escape(str(err))}</code>",
                reply_markup=admin_users_keyboard()
            )
        return

    if action == "forward_pool_upload":
        state.admin_pending_actions.pop(uid, None)
        status_msg = await m.answer(f"{ce(E_NUM_WAITING, '⏳')} Reading Number Forward Pool file...")
        try:
            file_info = await bot.get_file(m.document.file_id)
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
            try:
                resp = await _tg_client.get(file_url, timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0))
                resp.raise_for_status()
                file_bytes = resp.content
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError):
                async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)) as fallback_client:
                    resp = await fallback_client.get(file_url)
                    resp.raise_for_status()
                    file_bytes = resp.content

            filename = m.document.file_name or ""
            if os.path.splitext(filename)[1].lower() != ".txt":
                await status_msg.edit_text(
                    f"{ce(E_BROADCAST_FAIL, '❌')} Forward Pool requires a <code>.txt</code> file.",
                    reply_markup=admin_keyboard()
                )
                state.admin_temp_data.pop(uid, None)
                return

            numbers = []
            seen = set()
            content = file_bytes.decode("utf-8", errors="ignore")
            for line in content.splitlines():
                clean = clean_number(line.strip())
                if clean and clean not in seen:
                    seen.add(clean)
                    numbers.append({"number": clean, "country": country_name_for_number(clean)})

            if not numbers:
                await status_msg.edit_text(
                    f"{ce(E_BROADCAST_FAIL, '❌')} No valid phone numbers found in the uploaded file.",
                    reply_markup=admin_keyboard()
                )
                return

            state.admin_temp_data[uid] = {"numbers": numbers}
            state.admin_pending_actions[uid] = "forward_pool_service"
            country_counts = {}
            for item in numbers:
                country_counts[item["country"]] = country_counts.get(item["country"], 0) + 1
            country_summary = ", ".join(
                f"{html.escape(name)} ({count})" for name, count in sorted(country_counts.items())
            )
            await status_msg.edit_text(
                f"{ce(E_FWD_OK, '✅')} Found <b>{len(numbers)}</b> unique numbers.\n"
                f"{ce(E_OTP_GLOBE, '🏳️')} Countries: <b>{country_summary}</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} এখন এই pool-এর <b>service name</b> পাঠান "
                f"(যেমন <code>Facebook</code>, <code>WhatsApp</code>, <code>Custom SMS</code>)।",
                reply_markup=markup([btn("✖", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
            )
        except Exception as err:
            await status_msg.edit_text(
                f"{ce(E_BROADCAST_FAIL, '❌')} Failed to parse Number Forward Pool: <code>{html.escape(str(err))}</code>",
                reply_markup=admin_keyboard()
            )
        return

    if action != 'upload_stock':
        return

    # Remove the old "Upload Stock" prompt and the uploaded file bubble so
    # only the current processing/result message remains in the admin chat.
    await _delete_admin_flow_message(uid, "prompt_id")
    try:
        await m.delete()
    except Exception:
        pass

    state.admin_pending_actions.pop(uid, None)
    status_msg = await m.answer(f"{ce(E_NUM_WAITING, '⏳')} Downloading and parsing stock file...")
    await _track_admin_flow_message(uid, "status_id", status_msg)
    
    try:
        file_info = await bot.get_file(m.document.file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        try:
            resp = await _tg_client.get(file_url, timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0))
            resp.raise_for_status()
            file_bytes = resp.content
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError):
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)) as _fallback_client:
                resp = await _fallback_client.get(file_url)
                resp.raise_for_status()
                file_bytes = resp.content
        
        stock_items = []
        seen_numbers = set()
        skipped_rows = 0
        file_ext = os.path.splitext(m.document.file_name or "")[1].lower()

        def add_stock_item(raw_number, raw_country=None, raw_rate=None):
            nonlocal skipped_rows
            try:
                clean = clean_number(str(raw_number).strip()) if raw_number is not None else None
                if not clean or clean in seen_numbers:
                    skipped_rows += 1
                    return
                country = str(raw_country).strip() if raw_country else None
                rate = None
                if raw_rate not in (None, ""):
                    try:
                        rate = float(str(raw_rate).replace(",", ".").strip())
                        if rate < 0:
                            rate = None
                    except (TypeError, ValueError):
                        rate = None
                if not country:
                    key = _parse_country_cached(clean)
                    if key and key in COUNTRIES:
                        country = COUNTRIES[key][0].split('/')[0].strip()
                # Never discard an otherwise valid phone just because its
                # calling code is not in our country map. Keep it as Global.
                country = country or "Global"
                seen_numbers.add(clean)
                stock_items.append({"number": clean, "country": country, "otp_rate": rate})
            except Exception as row_err:
                skipped_rows += 1
                print(f"[StockUpload] Bad row ignored: {row_err}")

        if file_ext == '.txt':
            content = file_bytes.decode('utf-8', errors='ignore')
            for line in content.splitlines():
                raw = line.strip()
                if not raw:
                    continue
                parts = [x.strip() for x in re.split(r'[|,;\t]', raw)]
                if len(parts) >= 3:
                    add_stock_item(parts[0], parts[1], parts[2])
                elif len(parts) == 2 and not clean_number(parts[1]):
                    add_stock_item(parts[0], parts[1], None)
                elif len(parts) == 2:
                    add_stock_item(parts[0], None, parts[1])
                else:
                    add_stock_item(parts[0])
        elif file_ext in ['.xlsx', '.xls']:
            if file_ext == '.xls':
                raise ValueError('Legacy .xls is not supported. Please save it as .xlsx or .txt.')
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            sheet = wb.active
            for row in sheet.iter_rows(values_only=True):
                vals = list(row)
                if not vals:
                    continue
                # Supports columns: Number | Country | OTP Rate.
                if isinstance(vals[0], str) and vals[0].strip().lower() in {'number','phone','phone_number'}:
                    continue
                add_stock_item(vals[0], vals[1] if len(vals) > 1 else None, vals[2] if len(vals) > 2 else None)
        elif file_ext == '.csv':
            content = file_bytes.decode('utf-8-sig', errors='ignore')
            reader = csv.reader(io.StringIO(content))
            for vals in reader:
                if not vals:
                    continue
                if str(vals[0]).strip().lower() in {'number','phone','phone_number'}:
                    continue
                add_stock_item(vals[0], vals[1] if len(vals) > 1 else None, vals[2] if len(vals) > 2 else None)

        if not stock_items:
            await status_msg.edit_text(f"{ce(E_BROADCAST_FAIL, '❌')} No valid phone numbers found in the uploaded file.")
            return

        countries = sorted({x['country'] for x in stock_items})
        if len(countries) == 1:
            country_name = countries[0]
        else:
            country_name = "Mixed Countries"

        # Service is entered manually by text only. No predefined service list is shown.
        state.admin_temp_data[uid] = {"numbers": stock_items, "country": country_name}
        state.admin_pending_actions[uid] = "upload_stock_custom_svc"

        await status_msg.edit_text(
            f"{ce(E_RK_GET_NUM, '📱')} Found <b>{len(stock_items)}</b> valid phone numbers in stock file.\n"
            f"{ce(E_OTP_GLOBE, '🏳️')} Auto-detected Country: <b>{country_name}</b>\n"
            f"⚠️ Skipped/duplicate rows: <b>{skipped_rows}</b>\n\n"
            f"{ce(E_ADMIN_TOOL, '✏️')} <b>Type Service Name</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"এই stock কোন service-এর জন্য upload করবেন?\n"
            f"শুধু service-এর নাম লিখে পাঠান।\n\n"
            f"উদাহরণ: <code>Facebook</code>, <code>Telegram</code>, <code>Netflix</code>",
            reply_markup=markup([btn("❌ Cancel", E_BROADCAST_FAIL, callback_data="admin_panel", style="danger")]),
            parse_mode="HTML"
        )
    except Exception as e:
        await status_msg.edit_text(f"{ce(E_BROADCAST_FAIL, '❌')} Failed to parse stock file: {str(e)}")

# ================= PRIVATE MESSAGE HANDLER =================

@dp.message(F.chat.type == "private")
async def handle_private_message(m: types.Message):
    uid = m.from_user.id

    # Broadcast has highest priority for admin messages. This prevents stale
    # admin actions (withdrawal/provider/stock/etc.) from consuming the
    # broadcast message before it reaches the broadcaster.
    if await _run_broadcast(m):
        return

    # ── Admin Remove Balance Amount handler ──
    # Keep this in the general private-message handler: the older implementation
    # lived inside admin_userid_input, which only accepts 5-12 digit messages,
    # so ordinary amounts (e.g. 100) never reached it.
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "remove_balance_amount" and m.text:
        temp_data = state.admin_temp_data.get(uid) or {}
        target = temp_data.get("remove_balance_target")
        if not target:
            state.admin_pending_actions.pop(uid, None)
            state.admin_temp_data.pop(uid, None)
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please start again.",
                reply_markup=admin_keyboard()
            )
            return
        try:
            amount = float(m.text.strip().replace(",", "."))
            if not (amount > 0 and amount < float("inf")):
                raise ValueError
        except ValueError:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid amount!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} Send a positive amount, e.g. <code>100</code>.",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
            )
            return

        ok = await deduct_balance(int(target), amount)
        new_bal = await get_balance(int(target))
        if not ok:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Insufficient balance or update failed.</b>\n"
                f"Current balance: <b>{new_bal:.2f} TK</b>\n"
                f"Requested: <b>{amount:.2f} TK</b>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
            )
            return

        state.admin_pending_actions.pop(uid, None)
        state.admin_temp_data.pop(uid, None)
        new_bal = await get_balance(int(target))
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>Balance Removed!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>User ID:</b> <code>{int(target)}</code>\n"
            f"💰 <b>Removed:</b> <code>{amount:.2f} TK</code>\n"
            f"💵 <b>New Balance:</b> <code>{new_bal:.2f} TK</code>",
            reply_markup=admin_users_keyboard()
        )
        try:
            await bot.send_message(
                chat_id=int(target),
                text=(f"{ce(E_ADMIN_CASH, '💰')} <b>Balance Update</b>\n\n"
                      f"Admin deducted <b>{amount:.2f} TK</b> from your balance.\n"
                      f"New balance: <b>{new_bal:.2f} TK</b>")
            )
        except Exception as e:
            print(f"[Remove Balance Notify] Failed: {e}")
        return

    # ── Admin Support Username handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "set_support_username" and m.text:
        raw = m.text.strip()
        if raw.startswith("https://t.me/"):
            raw = raw.split("https://t.me/", 1)[1].split("/", 1)[0].strip()
        raw = raw.lstrip("@").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", raw):
            await m.answer(
                "❌ Invalid Telegram username.\n\nSend something like <code>@your_support</code>.",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_support_mgmt", style="danger")])
            )
            return
        await set_support_username(raw)
        state.admin_pending_actions.pop(uid, None)
        try: await m.delete()
        except Exception: pass
        await m.answer(
            f"✅ <b>Support Added/Updated</b>\n\n💬 Support: <code>@{raw}</code>",
            reply_markup=markup([btn("↩️ Support Management", E_TOOL_BACKBUTTON, callback_data="adm_support_mgmt", style="danger")])
        )
        return

    # ── Withdrawal Amount handler ──
    if uid in state.pending_withdrawal_amount and m.text:
        if not is_withdraw_enabled():
            state.pending_withdrawal_amount.pop(uid, None)
            state.pending_withdrawal_details.pop(uid, None)
            await m.answer("⚠️ Withdraw Disabled Right now")
            return
        raw_val = m.text.strip().replace(",", ".")
        bal = await get_balance(uid)
        info = state.pending_withdrawal_amount[uid]
        method = info["method"]
        details = info["details"]
        is_finance_withdrawal = str(method).lower().startswith("binance")
        requested_usd = None
        try:
            entered_amount = float(raw_val)
            if not (entered_amount > 0 and entered_amount < float("inf")):
                raise ValueError
        except ValueError:
            example = "1.00" if is_finance_withdrawal else "50"
            unit = "USD ($)" if is_finance_withdrawal else "TK"
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Invalid amount. Send a number, e.g. <code>{example}</code> {unit}.",
                reply_markup=markup([btn("↩️ Back", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
            )
            return

        min_wd = get_min_withdraw()
        if is_finance_withdrawal:
            requested_usd = entered_amount
            amount = round(usd_to_bdt(requested_usd), 2)
            minimum_usd = min_wd / get_usd_to_bdt_rate()
            if requested_usd < minimum_usd:
                await m.answer(
                    f"⚠️ Minimum Binance withdrawal is <code>${minimum_usd:.2f} USD</code>",
                    reply_markup=markup([btn("↩️ Back", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
                )
                return
            if amount > bal:
                await m.answer(
                    f"⚠️ Insufficient balance. Available Binance withdrawal: <code>${bdt_to_usd(bal):.2f} USD</code>",
                    reply_markup=markup([btn("↩️ Back", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
                )
                return
        else:
            amount = entered_amount
            if amount < min_wd:
                await m.answer(
                    f"⚠️ Minimum is <code>{min_wd:.2f} TK</code>",
                    reply_markup=markup([btn("↩️ Back", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
                )
                return
            if amount > bal:
                await m.answer(
                    f"⚠️ Insufficient balance, you have <code>{bal:.2f} TK</code>",
                    reply_markup=markup([btn("↩️ Back", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
                )
                return

        info = state.pending_withdrawal_amount.pop(uid)
        success = await deduct_balance(uid, amount)
        if not success:
            await m.answer("⚠️ Insufficient balance to process this withdrawal request.")
            return

        async with aiosqlite.connect(DB_FILE) as db:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await db.execute(
                "INSERT INTO withdrawals (user_id, method, details, amount, status, timestamp, requested_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, method, details, amount, 'pending', timestamp, requested_usd)
            )
            await db.commit()
            async with db.execute("SELECT last_insert_rowid()") as cursor:
                w_id = (await cursor.fetchone())[0]

        amount_display = (f"<code>${requested_usd:.2f} USD</code>" if requested_usd is not None else f"<code>{amount:.2f} TK</code>")
        admin_amount_display = (f"<code>${requested_usd:.2f} USD</code> (balance deduction: <code>{amount:.2f} TK</code>)" if requested_usd is not None else f"<code>{amount:.2f} TK</code>")
        await m.answer(
            f"✅ Withdrawal request: {amount_display} via {method} → "
            f"<code>{html.escape(details)}</code>, pending review"
        )

        admin_text = (
            f"🔔 <b>New Withdrawal Request!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>User ID:</b> <code>{uid}</code>\n"
            f"👤 <b>Username:</b> {m.from_user.mention_html() or 'None'}\n"
            f"💰 <b>Amount:</b> {admin_amount_display}\n"
            f"🏦 <b>Method:</b> <code>{html.escape(method)}</code>\n"
            f"📝 <b>Details:</b> <code>{html.escape(details)}</code>"
        )
        admin_kb = markup(
            [btn("Approve", E_FWD_OK, callback_data=f"adm_pay_appr_{w_id}", style="danger"),
             btn("Decline", E_BROADCAST_FAIL, callback_data=f"adm_pay_decl_{w_id}", style="danger")],
        )
        for admin_uid in {ADMIN_ID} | state.co_admin_ids:
            try:
                await bot.send_message(chat_id=admin_uid, text=admin_text, reply_markup=admin_kb)
            except Exception as e:
                print(f"[Admin Notify] Failed for {admin_uid}: {e}")
        return

    # ── Withdrawal Details handler ──
    if uid in state.pending_withdrawal_details and m.text:
        if not is_withdraw_enabled():
            state.pending_withdrawal_details.pop(uid, None)
            state.pending_withdrawal_method.pop(uid, None)
            state.pending_withdrawal_amount.pop(uid, None)
            await m.answer("⚠️ Withdraw Disabled Right now")
            return

        w_info = state.pending_withdrawal_details.pop(uid)
        details = m.text.strip()
        method = w_info["method"]
        if not details:
            await m.answer("⚠️ Please enter a valid number/UID.")
            state.pending_withdrawal_details[uid] = w_info
            return

        state.pending_withdrawal_amount[uid] = {
            "method": method,
            "details": details,
        }
        min_wd = get_min_withdraw()
        if str(method).lower().startswith("binance"):
            minimum_display = f"${(min_wd / get_usd_to_bdt_rate()):.2f} USD"
            available_display = f"${bdt_to_usd(await get_balance(uid)):.2f} USD"
            amount_instruction = "Enter Binance withdrawal amount in USD ($)"
        else:
            minimum_display = f"{min_wd:.2f} TK"
            available_display = f"{(await get_balance(uid)):.2f} TK"
            amount_instruction = "Enter withdrawal amount in TK"
        prompt = (
            f"💰 <b>{amount_instruction}</b>\n\n"
            f"🏦 Method: <b>{html.escape(method)}</b>\n"
            f"📝 Details: <code>{html.escape(details)}</code>\n"
            f"💳 Available: <code>{available_display}</code>\n"
            f"🔐 Minimum: <code>{minimum_display}</code>"
        )
        await m.answer(
            prompt,
            reply_markup=markup([btn("↩️ Back", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
        )
        return

    # ── DEMO OTP interval handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "demo_otp_interval" and m.text:
        raw_interval = m.text.strip()
        try:
            interval = int(raw_interval)
            if interval < 1 or interval > 3600:
                raise ValueError
        except ValueError:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Invalid interval. Send a whole number from <code>1</code> to <code>3600</code> seconds.",
                reply_markup=markup([btn("Back", E_TOOL_BACKBUTTON, suppress_auto_emoji=True, callback_data="adm_demo_otp_select", style="danger")]),
            )
            return

        state.demo_otp_interval = interval
        state.hadi_settings["demo_otp_interval"] = str(interval)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
                ("demo_otp_interval", str(interval)),
            )
            await db.commit()

        state.admin_pending_actions.pop(uid, None)
        await m.answer(
            f"✅ <b>OTP Interval set to {interval} seconds.</b>",
            reply_markup=demo_otp_selector_keyboard(await _demo_uploaded_pairs()),
        )
        return

    # ── Forward Pool: service name handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "forward_pool_service" and m.text:
        service_name = m.text.strip()
        if not service_name or len(service_name) > 80:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Service name must be between 1 and 80 characters. Try again."
            )
            return
        temp = state.admin_temp_data.get(uid)
        if not temp or not temp.get("numbers"):
            state.admin_pending_actions.pop(uid, None)
            await m.answer(f"{ce(E_BROADCAST_FAIL, '❌')} Session expired. Please upload the file again.", reply_markup=admin_keyboard())
            return
        temp["service"] = service_name
        state.admin_pending_actions[uid] = "forward_pool_interval"
        await m.answer(
            f"{ce(E_ADMIN_TOOL, '⏱')} <b>OTP Forward Interval</b>\n\n"
            f"Service: <b>{html.escape(svc_display_name(service_name))}</b>\n"
            f"কত seconds পর পর provider check করে নতুন OTP group-এ forward করবে?\n"
            f"<i>শুধু 1 থেকে 3600-এর মধ্যে একটি পূর্ণ সংখ্যা পাঠান।</i>",
            reply_markup=markup([btn("✖", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")])
        )
        return

    # ── Forward Pool: interval handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "forward_pool_interval" and m.text:
        raw_interval = m.text.strip()
        try:
            interval = int(raw_interval)
            if interval < 1 or interval > 3600:
                raise ValueError
        except ValueError:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Invalid interval. Send a whole number from <code>1</code> to <code>3600</code> seconds."
            )
            return
        temp = state.admin_temp_data.get(uid)
        if not temp or not temp.get("numbers") or not temp.get("service"):
            state.admin_pending_actions.pop(uid, None)
            await m.answer(f"{ce(E_BROADCAST_FAIL, '❌')} Session expired. Please upload the file again.", reply_markup=admin_keyboard())
            return
        temp["interval_seconds"] = interval
        temp["languages"] = []
        state.admin_pending_actions[uid] = "forward_pool_languages"
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} Interval set to <b>{interval} seconds</b>.\n\n"
            f"{ce(E_MENU_PIN, '📍')} <b>কোন কোন language-এ forward card দেখাতে চান?</b>\n"
            f"একাধিক language select করে <b>Done</b> চাপুন।",
            reply_markup=forward_pool_language_keyboard([])
        )
        return

    # ── Stock Upload: Custom Service Name Text handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == 'upload_stock_custom_svc' and m.text:
        await _delete_admin_flow_message(uid, "status_id")
        state.admin_pending_actions.pop(uid, None)
        service_name = m.text.strip()
        try: await m.delete()
        except Exception: pass

        temp_data = state.admin_temp_data.pop(uid, None)
        if not temp_data:
            await m.answer(f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please re-upload.", reply_markup=admin_keyboard())
            return

        if not service_name:
            await m.answer(f"{ce(E_BROADCAST_FAIL, '❌')} Invalid service name. Upload cancelled.", reply_markup=admin_keyboard())
            return

        state.admin_temp_data[uid] = {
            "numbers": temp_data["numbers"], "country": temp_data["country"],
            "service": service_name, "custom": True
        }

        # Ask for the upload-specific BDT rate before the emoji step.
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "INSERT OR IGNORE INTO earning_services (service_name, created_at, enabled) VALUES (?, ?, 1)",
                (service_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            await db.commit()

        state.admin_pending_actions[uid] = "upload_stock_rate"
        rate_prompt = await m.answer(
            f"{ce(E_ADMIN_CASH, '💰')} <b>Set OTP Rate</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Service: <b>{svc_display_name(service_name)}</b>\n\n"
            f"এই upload-এর সব number-এর প্রতি successful OTP-এর রেট টাকা হিসেবে পাঠান।\n"
            f"উদাহরণ: <code>1.25</code> টাকা।\n\n"
            f"📌 এই rate upload-এর সাথে save হবে — পরে আবার rate set করতে হবে না.",
            reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_upload_stock", style="danger")])
        )
        await _track_admin_flow_message(uid, "rate_id", rate_prompt)
        return

    # ── Stock Upload: Emoji Picker — typed service name (case-insensitive
    #    match, avoids clicking through many pages) ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) in ('upload_stock_emoji_type', 'upload_stock_emoji_after_custom') and m.text:
        typed = m.text.strip()
        emoji_key = match_emoji_key_by_text(typed)

        if not emoji_key:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Match পাওয়া যায়নি!</b> এই নামে কোনো service সেট নেই।\n"
                f"সঠিক বানানে আবার লিখুন (e.g. <code>facebook</code>, <code>netflix</code>), "
                f"অথবা লিস্ট থেকে বেছে নিতে নিচের বাটন চাপুন।",
                reply_markup=markup([btn("↩️ Back to List", E_TOOL_BACKBUTTON, callback_data="svcemoji_pg_0", style="danger")])
            )
            return

        state.admin_pending_actions.pop(uid, None)
        temp_data = state.admin_temp_data.pop(uid, None)
        if not temp_data:
            await m.answer(f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please re-upload.", reply_markup=admin_keyboard())
            return

        await set_service_emoji(temp_data["service"], emoji_key)
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>{svc_display_name(temp_data['service'])}</b> এখন থেকে "
            f"{_icon_for_emoji_key(emoji_key)} <b>{_MAIN_SVC_NAME.get(emoji_key, emoji_key.title())}</b> "
            f"emoji দিয়ে forward হবে।"
        )
        await _finalize_stock_upload(m, temp_data)
        return

    # ── Stock Upload: OTP Rate handler ──
    # The rate is entered once during upload and saved directly into numbers.
    # No separate stock-rate setup is needed afterwards.
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "upload_stock_rate" and m.text:
        await _delete_admin_flow_message(uid, "rate_id")
        await _delete_admin_flow_message(uid, "status_id")
        temp_data = state.admin_temp_data.get(uid)
        try: await m.delete()
        except Exception: pass
        if not temp_data:
            state.admin_pending_actions.pop(uid, None)
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Session expired. Please upload the file again.",
                reply_markup=admin_keyboard()
            )
            return

        raw_val = m.text.strip().replace(",", ".")
        try:
            upload_rate = float(raw_val)
            if upload_rate <= 0:
                raise ValueError
        except ValueError:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid rate!</b>\n\n"
                f"শুধু 0-এর বেশি টাকার রেট পাঠান, যেমন <code>1.25</code>।",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_upload_stock", style="danger")])
            )
            return

        temp_data["upload_rate"] = upload_rate
        state.admin_pending_actions.pop(uid, None)

        # Service emoji is detected automatically from the uploaded service name.
        # If the name is not an exact known service, _resolve_service_icon uses the default icon.
        if temp_data.get("custom", False):
            detected_key = match_emoji_key_by_text(str(temp_data.get("service", "")))
            if detected_key:
                await set_service_emoji(temp_data["service"], detected_key)

        state.admin_temp_data.pop(uid, None)
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} Upload rate saved: <b>{format_dual_rate(upload_rate)}</b> / OTP\n"
            f"{ce(E_MENU_PIN, '📋')} Service emoji will be detected automatically."
        )
        await _finalize_stock_upload(m, temp_data)
        return

    # ── Add Balance Amount handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "add_balance_amount" and m.text:
        state.admin_pending_actions.pop(uid, None)
        temp_data = state.admin_temp_data.pop(uid, None)
        target = temp_data.get("add_balance_target") if temp_data else None

        if not target:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please start again.",
                reply_markup=admin_keyboard()
            )
            return

        raw_val = m.text.strip().replace(",", ".")
        try:
            amount = float(raw_val)
            if amount == 0:
                raise ValueError
        except ValueError:
            state.admin_temp_data[uid] = {"add_balance_target": target}
            state.admin_pending_actions[uid] = "add_balance_amount"
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid amount!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Send a non-zero number, e.g. 100 or -50</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="danger")])
            )
            return

        await add_balance(target, amount)
        new_bal = await get_balance(target)
        action_word = "Added" if amount > 0 else "Deducted"

        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>Balance Updated!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>User ID:</b> <code>{target}</code>\n"
            f"💰 <b>{action_word}:</b> <code>{abs(amount):.2f} TK</code>\n"
            f"💵 <b>New Balance:</b> <code>{new_bal:.2f} TK</code>",
            reply_markup=admin_keyboard()
        )

        try:
            notice = "added to" if amount > 0 else "deducted from"
            await bot.send_message(
                chat_id=target,
                text=(
                    f"{ce(E_FWD_OK, '✅')} <b>Balance Update</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"💰 <b>{abs(amount):.2f} TK</b> has been {notice} your balance by admin.\n"
                    f"💵 <b>New Balance:</b> <code>{new_bal:.2f} TK</code>"
                )
            )
        except Exception as e:
            print(f"[Add Balance Notify] Failed: {e}")
        return

    # ── Delete single Number Stock text handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "delete_number_stock" and m.text:
        state.admin_pending_actions.pop(uid, None)
        raw = m.text.strip()
        number = clean_number(raw) if raw else ""

        if not number:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Invalid phone number.",
                reply_markup=markup([
                    btn("Try Again", E_BROADCAST_FAIL, callback_data="adm_delete_number", style="danger"),
                    btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")
                ])
            )
            return

        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                "SELECT phone_number, service, country, status FROM numbers WHERE phone_number = ?",
                (number,)
            ) as cur:
                row = await cur.fetchone()

            if not row:
                await m.answer(
                    f"{ce(E_BROADCAST_FAIL, '❌')} Number <code>{html.escape(number)}</code> was not found in stock.",
                    reply_markup=markup([
                        btn("Try Again", E_BROADCAST_FAIL, callback_data="adm_delete_number", style="danger"),
                        btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="danger")
                    ])
                )
                return

            await db.execute("DELETE FROM numbers WHERE phone_number = ?", (number,))
            await db.commit()

        _, service, country, status = row
        await m.answer(
            f"{ce(E_BROADCAST_DONE, '✅')} <b>Number deleted successfully.</b>\n\n"
            f"📱 <code>{html.escape(number)}</code>\n"
            f"🏷️ Service: <b>{html.escape(str(service))}</b>\n"
            f"🏳️ Country: <b>{html.escape(str(country))}</b>\n"
            f"📌 Previous status: <b>{html.escape(str(status))}</b>",
            reply_markup=admin_keyboard()
        )
        return

    # ── Country Rate Text handlers ──
    action_country = state.admin_pending_actions.get(uid)
    if is_admin(uid) and action_country and m.text:
        if action_country.startswith("set_global_country_rate|"):
            state.admin_pending_actions.pop(uid, None)
            country = action_country.split("|", 1)[1]
            raw_val = m.text.strip().replace(",", ".")
            try:
                new_rate = float(raw_val)
                if new_rate < 0:
                    raise ValueError
            except ValueError:
                await m.answer("❌ Invalid rate! টাকায় রেট পাঠান, যেমন <code>1.50</code>।", reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_cr_global", style="danger")]))
                return
            await set_global_country_rate(country, new_rate)
            status = f"<b>{format_dual_rate(new_rate)}</b> / OTP" if new_rate > 0 else "<i>OFF (global rate disabled)</i>"
            await m.answer(f"✅ <b>Global — {country}</b> rate updated!\n\nNew rate: {status}", reply_markup=markup([btn("↩️ Back to Global Country Rates", E_MENU_GLOBE2, callback_data="adm_cr_global", style="danger")]))
            return

        if action_country.startswith("set_country_rate|"):
            state.admin_pending_actions.pop(uid, None)
            parts = action_country.split("|", 2)
            if len(parts) != 3:
                return
            svc, country = parts[1], parts[2]
            raw_val = m.text.strip().replace(",", ".")
            try:
                new_rate = float(raw_val)
                if new_rate < 0:
                    raise ValueError
            except ValueError:
                await m.answer("❌ Invalid country rate! টাকায় রেট পাঠান, যেমন <code>1.50</code>।", reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data=f"adm_cr_svc|{svc}", style="danger")]))
                return
            await set_country_rate(svc, country, new_rate)
            status = f"<b>{format_dual_rate(new_rate)}</b> / OTP" if new_rate > 0 else "<i>OFF (service-specific rate disabled; global fallback will apply)</i>"
            await m.answer(f"✅ <b>{svc_display_name(svc)} — {country}</b> country rate updated!\n\nNew rate: {status}", reply_markup=markup([btn("↩️ Back to Service Country Rates", E_MENU_GLOBE2, callback_data=f"adm_cr_svc|{svc}", style="danger")]))
            return

    # ── Add unlimited earning service: Step 1 — service name ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "add_earning_service" and m.text:
        svc = m.text.strip()
        if not svc or len(svc) > 64:
            state.admin_pending_actions.pop(uid, None)
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Invalid service name. Use 1–64 characters.",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
            )
            return

        svc_key = _rate_key(svc)
        if svc_key in {_rate_key(s) for s in RATE_SERVICES}:
            state.admin_pending_actions[uid] = f"set_rate_{svc_key}"
            await m.answer(
                f"{ce(E_MENU_PIN, '📌')} <b>{svc_display_name(svc)}</b> is already a built-in service.\n\n"
                f"প্রতি সফল OTP-এর নতুন রেট টাকা হিসেবে পাঠান (যেমন <code>1.50</code>)।",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
            )
            return

        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "INSERT OR REPLACE INTO earning_services (service_name, created_at, enabled) VALUES (?, COALESCE((SELECT created_at FROM earning_services WHERE service_name = ?), ?), 1)",
                (svc, svc, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            await db.commit()

        state.admin_pending_actions[uid] = f"set_rate_c:{svc}"
        await m.answer(
            f"{ce(E_ADMIN_CASH, '💰')} <b>{html.escape(svc)}</b> — Earning Rate\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"প্রতি সফল OTP-এর আয়ের পরিমাণ টাকা হিসেবে পাঠান।\n"
            f"উদাহরণ: <code>1.25</code> টাকা।\n\n"
            f"Send <code>0</code> to disable earning.",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
        )
        return

    # ── Earning Rate Text handler (fixed services + custom services) ──
    action_rate = state.admin_pending_actions.get(uid)
    if (is_admin(uid) and action_rate and action_rate.startswith("set_rate_") and m.text
            and (action_rate[len("set_rate_"):] in RATE_SERVICES or action_rate.startswith("set_rate_c:"))):
        state.admin_pending_actions.pop(uid, None)
        svc = action_rate[len("set_rate_c:"):] if action_rate.startswith("set_rate_c:") else action_rate[len("set_rate_"):]
        raw_val = m.text.strip().replace(",", ".")

        try:
            new_rate = float(raw_val)
            if new_rate < 0:
                raise ValueError
        except ValueError:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid rate!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a valid amount in TK, e.g. 1.50</i>",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
            )
            return

        await set_service_rate(svc, new_rate)

        status_line = f"<b>{format_dual_rate(new_rate)}</b> / OTP" if new_rate > 0 else "<i>OFF (earning disabled)</i>"
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>{svc_display_name(svc)} rate updated!</b>\n\n"
            f"New rate: {status_line}",
            reply_markup=await admin_rates_keyboard()
        )
        return

    # ── Profile display conversion-rate text handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "set_usd_exchange_rate" and m.text:
        raw_val = m.text.strip().replace(",", ".")
        try:
            exchange_rate = float(raw_val)
            if exchange_rate <= 0:
                raise ValueError
        except ValueError:
            await m.answer(
                "❌ ভুল রেট। ০-এর বেশি একটি সংখ্যা পাঠান, যেমন <code>125</code>।",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")])
            )
            return
        await set_usd_to_bdt_rate(exchange_rate)
        state.admin_pending_actions.pop(uid, None)
        await m.answer(
            f"✅ প্রোফাইলের হিসাবের রেট আপডেট হয়েছে: <b>৳{exchange_rate:g}</b>",
            reply_markup=await admin_rates_keyboard()
        )
        return

    # ── Referral commission/bonus settings text handler ──
    referral_action = state.admin_pending_actions.get(uid)
    if is_admin(uid) and referral_action in {"set_referral_commission", "set_referral_bonus"} and m.text:
        raw_val = m.text.strip().replace(",", ".").replace("%", "")
        try:
            new_value = float(raw_val)
            if new_value < 0 or (referral_action == "set_referral_commission" and new_value > 100):
                raise ValueError
        except ValueError:
            expected = "0 থেকে 100-এর মধ্যে শতাংশ (যেমন 5)" if referral_action == "set_referral_commission" else "0 বা তার বেশি টাকা (যেমন 1.50)"
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>ভুল মান!</b>\n\nসঠিক মান পাঠান: {expected}",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_referral_settings", style="danger")])
            )
            return
        if referral_action == "set_referral_commission":
            await set_referral_setting("referral_commission_rate", new_value / 100.0)
            label = f"Lifetime commission updated to <b>{new_value:.2f}%</b>."
        else:
            await set_referral_setting("referral_bonus_per_refer", new_value)
            label = f"Referral bonus updated to <b>{new_value:.2f} TK</b>."
        state.admin_pending_actions.pop(uid, None)
        await m.answer(f"{ce(E_FWD_OK, '✅')} {label}\n\nপরিবর্তনটি ডাটাবেজে সংরক্ষণ করা হয়েছে।", reply_markup=markup(
            [btn("🔗 Referral Settings", E_RK_REFER_EARN, callback_data="adm_referral_settings", style="danger")],
            [btn("↩️ Back to Rates", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="danger")],
        ))
        return

    # ── Minimum Withdraw Text handler ──
    min_wd_action = state.admin_pending_actions.get(uid)
    if is_admin(uid) and min_wd_action in {"set_min_withdraw", "set_min_withdraw_direct"} and m.text:
        state.admin_pending_actions.pop(uid, None)
        direct_from_admin_panel = min_wd_action == "set_min_withdraw_direct"
        back_callback = "admin_panel" if direct_from_admin_panel else "adm_rates"
        raw_val = m.text.strip().replace(",", ".")

        try:
            new_min = float(raw_val)
            if new_min < 0:
                raise ValueError
        except ValueError:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid amount!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a valid number, e.g. 10</i>",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data=back_callback, style="danger")])
            )
            return

        await set_min_withdraw(new_min)

        if direct_from_admin_panel:
            reply_markup = admin_keyboard()
        else:
            reply_markup = await admin_rates_keyboard()

        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>Minimum withdrawal updated!</b>\n\n"
            f"New minimum: <b>{new_min:.2f} TK</b>",
            reply_markup=reply_markup
        )
        return

    # ── Add Panel wizard: Step 1 — Name ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addpanel_name" and m.text:
        raw_name = m.text.strip()
        await _delete_admin_input_message(m)
        key = sanitize_panel_key(raw_name)

        if not key or key in _RESERVED_PANEL_KEYS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid name!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name (letters/numbers only).</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="danger")])
            )
            return
        if key in SMS_PROVIDERS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>A panel with this name already exists!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name.</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="danger")])
            )
            return

        state.admin_temp_data[uid] = {"key": key, "label": raw_name}
        state.admin_pending_actions[uid] = "addpanel_token"
        await _delete_admin_flow_message(uid, "prompt_id")
        next_prompt = await m.answer(
            f"{ce(E_OTP_KEY, '🔑')} <b>{raw_name} — API Token</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} এই panel-টার API token পাঠান:\n\n"
            f"<i>Step 2/3 — Token</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="danger")])
        )
        await _track_admin_flow_message(uid, "prompt_id", next_prompt)
        return

    # ── Add Panel wizard: Step 2 — API Token ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addpanel_token" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["token"] = m.text.strip()
        await _delete_admin_input_message(m)
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addpanel_url"
        await _delete_admin_flow_message(uid, "prompt_id")
        next_prompt = await m.answer(
            f"{ce(E_MENU_GLOBE2, '🗺️')} <b>{pending.get('label','Panel')} — API URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} এই panel-টার viewstats API URL পাঠান:\n\n"
            f"<i>Step 3/3 — URL</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="danger")])
        )
        await _track_admin_flow_message(uid, "prompt_id", next_prompt)
        return

    # ── Add Panel wizard: Step 3 — API URL (finalize, spawn monitor loop) ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addpanel_url" and m.text:
        state.admin_pending_actions.pop(uid, None)
        pending = state.admin_temp_data.pop(uid, None)
        url = m.text.strip()
        await _delete_admin_input_message(m)
        await _delete_admin_flow_message(uid, "prompt_id")

        if not pending or "key" not in pending or "token" not in pending:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please start again with Add Panel.",
                reply_markup=admin_panel_mgmt_keyboard()
            )
            return

        await add_dynamic_panel(pending["key"], pending["label"], pending["token"], url)

        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>{pending['label']} panel added!</b>\n\n"
            f"এটা এখন বাকি panel গুলোর সাথে (Hadi/Lamix/CoreSMS ইত্যাদি) parallel ভাবে চলছে — "
            f"restart এর দরকার হয়নি। Default check interval: <b>1s</b> (Interval বাটন থেকে বদলানো যাবে)।",
            reply_markup=admin_panel_mgmt_keyboard()
        )
        return

    # ── Force Join — Add Channel wizard: Step 1 — Name ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addfj_label" and m.text:
        raw_name = m.text.strip()
        key = sanitize_force_join_key(raw_name)

        if not key:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid name!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different name (letters/numbers only).</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="danger")])
            )
            return
        if key in state.force_join_channels or key in BASE_FORCE_JOIN_KEYS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>এই নামে একটা channel আগে থেকেই আছে!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>একটা ভিন্ন নাম পাঠান।</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="danger")])
            )
            return

        state.admin_temp_data[uid] = {"key": key, "label": raw_name}
        state.admin_pending_actions[uid] = "addfj_chatid"
        await m.answer(
            f"{ce(E_JOIN_CHANNEL, '🆔')} <b>{raw_name} — Chat ID / Username</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Channel/group এর @username অথবা -100... chat ID পাঠান "
            f"(bot-কে ওই channel/group এ admin হিসেবে যোগ করা লাগবে, নাহলে join check কাজ করবে না):\n\n"
            f"<i>Step 2/3 — Chat ID</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="danger")])
        )
        return

    # ── Force Join — Add Channel wizard: Step 2 — Chat ID ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addfj_chatid" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        chat_id_raw = m.text.strip()
        pending["chat_id"] = chat_id_raw
        # @username দিলে সেটা দিয়েই default join URL বানিয়ে suggest করা হয়;
        # numeric chat ID হলে (private group) admin-কেই পরের step এ URL দিতে হবে।
        pending["default_url"] = f"https://t.me/{chat_id_raw[1:]}" if chat_id_raw.startswith("@") else ""
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addfj_url"

        hint = f" (অথবা <code>skip</code> লিখে <code>{pending['default_url']}</code> ব্যবহার করুন)" if pending["default_url"] else ""
        await m.answer(
            f"{ce(E_MENU_GLOBE2, '🗺️')} <b>{pending.get('label','Channel')} — Join URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} এই channel/group এর invite/join URL পাঠান{hint}:\n\n"
            f"<i>Step 3/3 — URL</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="danger")])
        )
        return

    # ── Force Join — Add Channel wizard: Step 3 — URL (finalize) ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addfj_url" and m.text:
        state.admin_pending_actions.pop(uid, None)
        pending = state.admin_temp_data.pop(uid, None)
        typed = m.text.strip()

        if not pending or "key" not in pending or "chat_id" not in pending:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please start again with Add Channel.",
                reply_markup=force_join_mgmt_keyboard()
            )
            return

        url = pending.get("default_url", "") if typed.lower() == "skip" else typed
        if not url:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid URL!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>একটা valid URL পাঠান, অথবা @username দিয়ে আবার Chat ID step থেকে শুরু করুন।</i>",
                reply_markup=force_join_mgmt_keyboard()
            )
            return

        saved = await add_force_join_channel(pending["key"], pending["chat_id"], pending["label"], url)
        if not saved:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid channel details or join URL.</b> Please use a valid https://t.me/... URL.",
                reply_markup=force_join_mgmt_keyboard()
            )
            return

        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>{pending['label']} added to Force Join!</b>\n\n"
            f"পরের /start থেকেই user-দের এই channel/group জয়েন করতে হবে — restart এর দরকার নেই।",
            reply_markup=force_join_mgmt_keyboard()
        )
        return

    # ── Force Join — Edit URL text handler ──
    action_fj_url = state.admin_pending_actions.get(uid)
    if is_admin(uid) and action_fj_url and action_fj_url.startswith("set_fj_url_") and m.text:
        key = action_fj_url[len("set_fj_url_"):]
        state.admin_pending_actions.pop(uid, None)
        ch = state.force_join_channels.get(key)
        if not ch:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Channel not found, session expired.",
                reply_markup=force_join_mgmt_keyboard()
            )
            return
        new_url = m.text.strip()
        saved = await add_force_join_channel(key, ch["chat_id"], ch["label"], new_url)
        if not saved:
            await m.answer("❌ Invalid URL. Please use https://t.me/...", reply_markup=force_join_detail_keyboard(key))
            return

        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>{ch['label']} URL updated!</b>\n\n"
            f"New URL: <code>{new_url}</code>",
            reply_markup=force_join_detail_keyboard(key)
        )
        return

    # ── Add Auto Captcha Panel wizard: Step 1 — Name ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_name" and m.text:
        raw_name = m.text.strip()
        key = sanitize_panel_key(raw_name)

        if not key or key in _RESERVED_PANEL_KEYS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid name!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name (letters/numbers only).</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
            )
            return
        if key in CAPTCHA_PANELS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>A panel with this name already exists!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name.</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
            )
            return

        state.admin_temp_data[uid] = {"key": key, "label": raw_name}
        state.admin_pending_actions[uid] = "addcap_login_url"
        await m.answer(
            f"{ce(E_MENU_GLOBE2, '🗺️')} <b>{raw_name} — Login URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Panel-টার login page link পাঠান (e.g. <code>http://panel.example.com/login</code>):\n\n"
            f"<i>Step 2/9 — Login URL</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
        )
        return

    # ── Step 2 — Login URL ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_login_url" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["login_url"] = m.text.strip()
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addcap_username"
        await m.answer(
            f"{ce(E_ADMIN_USERS, '👤')} <b>{pending.get('label','Panel')} — Username</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Panel login username পাঠান:\n\n"
            f"<i>Step 3/9 — Username</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
        )
        return

    # ── Step 3 — Username ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_username" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["username"] = m.text.strip()
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addcap_password"
        await m.answer(
            f"{ce(E_MENU_LOCK, '🔐')} <b>{pending.get('label','Panel')} — Password</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Panel login password পাঠান:\n\n"
            f"<i>Step 4/9 — Password</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
        )
        return

    # ── Step 4 — Password ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_password" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["password"] = m.text.strip()
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addcap_msg_link"
        await m.answer(
            f"{ce(E_MENU_GLOBE2, '🗺️')} <b>{pending.get('label','Panel')} — SMS/CDR Page Link</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Login-এর পর যে page-এ SMS টেবিল দেখায়, তার link পাঠান।\n"
            f"না থাকলে <code>skip</code> লিখুন — bot নিজে auto-detect করবে।\n\n"
            f"<i>Step 5/9 — Msg Link</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
        )
        return

    # ── Step 5 — Msg Link ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_msg_link" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        val = m.text.strip()
        pending["msg_link"] = "" if val.lower() == "skip" else val
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addcap_num_col_name"
        await m.answer(
            f"{ce(E_NUM_PHONE, '📱')} <b>{pending.get('label','Panel')} — Number Column Name</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} টেবিলে number column-এর header নাম পাঠান (e.g. <code>number</code>):\n\n"
            f"<i>Step 6/8 — Num Column Name</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
        )
        return

    # ── Step 6 — Num Column Name ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_num_col_name" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["num_col_name"] = m.text.strip() or "number"
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addcap_num_col_idx"
        await m.answer(
            f"{ce(E_NUM_PHONE, '🔢')} <b>{pending.get('label','Panel')} — Number Column Serial</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} টেবিলে number column-টা কত নম্বর serial-এ আছে পাঠান (e.g. <code>3</code>):\n"
            f"ঠিক না জানলে <code>skip</code> লিখুন — default <code>1</code> বসবে, আর bot header-নাম দেখে নিজে position বের করার চেষ্টা করবে।\n\n"
            f"<i>Step 7/9 — Num Column Serial</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
        )
        return

    # ── Step 7 — Num Column Serial (idx) ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_num_col_idx" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        val = m.text.strip()
        if val.lower() == "skip":
            pending["num_col_idx"] = 1
        elif val.isdigit() and int(val) >= 1:
            pending["num_col_idx"] = int(val)
        else:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid serial!</b> শুধু সংখ্যা পাঠান (e.g. <code>3</code>) অথবা <code>skip</code> লিখুন।",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
            )
            return
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addcap_msg_col_name"
        await m.answer(
            f"{ce(E_OTP_KEY, '💬')} <b>{pending.get('label','Panel')} — Message Column Name</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} টেবিলে message column-এর header নাম পাঠান (e.g. <code>message</code>):\n\n"
            f"<i>Step 8/9 — Msg Column Name</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
        )
        return

    # ── Step 8 — Msg Column Name ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_msg_col_name" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["msg_col_name"] = m.text.strip() or "message"
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addcap_msg_col_idx"
        await m.answer(
            f"{ce(E_OTP_KEY, '🔢')} <b>{pending.get('label','Panel')} — Message Column Serial</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} টেবিলে message column-টা কত নম্বর serial-এ আছে পাঠান (e.g. <code>5</code>):\n"
            f"ঠিক না জানলে <code>skip</code> লিখুন — default <code>2</code> বসবে, আর bot header-নাম দেখে নিজে position বের করার চেষ্টা করবে।\n\n"
            f"<i>Step 9/9 — Msg Column Serial</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
        )
        return

    # ── Step 9 — Msg Column Serial (idx) — finalize, save & spawn monitor ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_msg_col_idx" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        val = m.text.strip()
        if val.lower() == "skip":
            pending["msg_col_idx"] = 2
        elif val.isdigit() and int(val) >= 1:
            pending["msg_col_idx"] = int(val)
        else:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid serial!</b> শুধু সংখ্যা পাঠান (e.g. <code>5</code>) অথবা <code>skip</code> লিখুন।",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="danger")])
            )
            return

        state.admin_pending_actions.pop(uid, None)
        pending = state.admin_temp_data.pop(uid, None) or pending

        if not pending or "key" not in pending or "login_url" not in pending:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please start again with Add Auto Captcha Panel.",
                reply_markup=captcha_panel_mgmt_keyboard()
            )
            return

        await add_captcha_panel(
            pending["key"], pending["label"], pending["login_url"], pending["username"], pending["password"],
            msg_link=pending.get("msg_link", ""),
            num_col_name=pending.get("num_col_name", "number"), num_col_idx=pending.get("num_col_idx", 1),
            msg_col_name=pending.get("msg_col_name", "message"), msg_col_idx=pending.get("msg_col_idx", 2)
        )

        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>{pending['label']} Auto Captcha Panel added!</b>\n\n"
            f"Bot এখন background-এ automatically captcha solve করে login করবে এবং SMS টেবিল monitor শুরু করবে — "
            f"restart এর দরকার নেই। Status দেখতে panel-এ ঢুকে Retry Login চাপুন।",
            reply_markup=captcha_panel_mgmt_keyboard()
        )
        return

    # ── Add Green Panel wizard: Step 1 — Name ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addgp_name" and m.text:
        raw_name = m.text.strip()
        key = sanitize_panel_key(raw_name)

        if not key or key in _RESERVED_PANEL_KEYS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid name!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name (letters/numbers only).</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="danger")])
            )
            return
        if key in CAPTCHA_PANELS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>A panel with this name already exists!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name.</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="danger")])
            )
            return

        state.admin_temp_data[uid] = {"key": key, "label": raw_name}
        state.admin_pending_actions[uid] = "addgp_login_url"
        await m.answer(
            f"{ce(E_MENU_GLOBE2, '🗺️')} <b>{raw_name} — Login URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Green Panel-টার base URL পাঠান (e.g. <code>http://143.110.245.86/</code>):\n\n"
            f"<i>Step 2/4 — Login URL</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="danger")])
        )
        return

    # ── Step 2 — Login URL ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addgp_login_url" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["login_url"] = m.text.strip()
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addgp_username"
        await m.answer(
            f"{ce(E_ADMIN_USERS, '👤')} <b>{pending.get('label','Panel')} — Username</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Panel login username পাঠান:\n\n"
            f"<i>Step 3/4 — Username</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="danger")])
        )
        return

    # ── Step 3 — Username ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addgp_username" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["username"] = m.text.strip()
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addgp_password"
        await m.answer(
            f"{ce(E_MENU_LOCK, '🔐')} <b>{pending.get('label','Panel')} — Password</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Panel login password পাঠান:\n\n"
            f"<i>Step 4/4 — Password</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="danger")])
        )
        return

    # ── Step 4 — Password — finalize, save & spawn monitor ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addgp_password" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["password"] = m.text.strip()

        state.admin_pending_actions.pop(uid, None)
        pending = state.admin_temp_data.pop(uid, None) or pending

        if not pending or "key" not in pending or "login_url" not in pending:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please start again with Add Green Panel.",
                reply_markup=green_panel_mgmt_keyboard()
            )
            return

        await add_captcha_panel(
            pending["key"], pending["label"], pending["login_url"], pending["username"], pending["password"],
            panel_type="greennews"
        )

        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>{pending['label']} Green Panel added!</b>\n\n"
            f"Bot এখন background-এ automatically captcha solve করে login করবে এবং SMS পড়া শুরু করবে — "
            f"restart এর দরকার নেই। Status দেখতে panel-এ ঢুকে Retry Login চাপুন।",
            reply_markup=green_panel_mgmt_keyboard()
        )
        return

    # ── Multi-Provider (Hadi / Lamix / CoreSMS) API Settings Text handler ──
    action_provider = state.admin_pending_actions.get(uid)
    if (is_admin(uid) and action_provider and action_provider.startswith("set_") and m.text
            and action_provider.count("_") == 2
            and action_provider.split("_", 2)[1] in SMS_PROVIDERS):
        state.admin_pending_actions.pop(uid, None)
        val = m.text.strip()
        _, provider, field = action_provider.split("_", 2)

        db_key = f"{provider}_api_{field}" if field in ("token", "url") else f"{provider}_check_interval"

        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (db_key, val))
            await db.commit()

        state.hadi_settings[db_key] = val

        plabel = PROVIDER_LABELS.get(provider, provider.capitalize())
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>{plabel} {field.capitalize()} updated!</b>\n\n"
            f"New value: <code>{val}</code>",
            reply_markup=admin_panel_detail_keyboard(provider)
        )
        return

    # Broadcast is handled at the top of this handler via _run_broadcast().
    return

# ================= MAIN =================
# ================= DEMO OTP ADMIN CALLBACKS =================
@dp.callback_query(F.data == "adm_demo_otp")
async def adm_demo_otp_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    await safe_edit(c.message, demo_otp_admin_text(), demo_otp_admin_keyboard())


@dp.callback_query(F.data == "adm_demo_otp_select")
async def adm_demo_otp_select_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True); return
    pairs = await _demo_uploaded_pairs()
    if not pairs:
        await c.answer("❌ No uploaded Service/Country found.", show_alert=True); return
    await c.answer()
    await safe_edit(c.message, "<b>Select</b>", demo_otp_selector_keyboard(pairs))


@dp.callback_query(F.data.startswith("adm_demo_pair_"))
async def adm_demo_pair_toggle_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True); return
    pairs = await _demo_uploaded_pairs()
    try:
        service, country = pairs[int(c.data.rsplit("_", 1)[1])]
    except (ValueError, IndexError):
        await c.answer("Invalid selection", show_alert=True); return
    # Keep selections scoped to the exact uploaded Service/Country pair.
    # This prevents selecting Facebook/Pakistan from accidentally enabling
    # Facebook/Zimbabwe or WhatsApp/Pakistan.
    pair = (str(service).strip(), str(country).strip())
    if not hasattr(state, "demo_otp_pairs"):
        state.demo_otp_pairs = set()
    if pair in state.demo_otp_pairs:
        state.demo_otp_pairs.discard(pair)
    else:
        state.demo_otp_pairs.add(pair)
    await c.answer()
    await safe_edit(c.message, "<b>Select</b>", demo_otp_selector_keyboard(pairs))


@dp.callback_query(F.data == "adm_demo_interval")
async def adm_demo_interval_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    state.admin_pending_actions[c.from_user.id] = "demo_otp_interval"
    await c.answer()
    await safe_edit(
        c.message,
        f"⏱ <b>Set OTP Interval</b>\n\n"
        f"কত seconds পর পর 1টি DEMO OTP পাঠানো হবে?\n"
        f"বর্তমান: <b>{int(getattr(state, 'demo_otp_interval', 30))} seconds</b>\n\n"
        f"<i>1 থেকে 3600-এর মধ্যে একটি পূর্ণ সংখ্যা পাঠান।</i>",
        markup([btn("Back", E_TOOL_BACKBUTTON, suppress_auto_emoji=True, callback_data="adm_demo_otp_select", style="danger")]),
    )


@dp.callback_query(F.data == "adm_demo_select_confirm")
async def adm_demo_select_confirm_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True); return
    # Exact-pair selection is the only active filter now.
    state.demo_otp_services.clear()
    state.demo_otp_countries.clear()
    await c.answer("✅ Selected")
    await safe_edit(c.message, demo_otp_admin_text(), demo_otp_admin_keyboard())


@dp.callback_query(F.data == "adm_demo_otp_noop")
async def adm_demo_otp_noop_cb(c: types.CallbackQuery):
    await c.answer()


@dp.callback_query(F.data == "adm_demo_otp_start")
async def adm_demo_otp_start_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    if not (OTP_GROUP_ID):
        await c.answer("❌ DEMO group is not configured.", show_alert=True)
        return
    options = await _demo_uploaded_options()
    if not options:
        await c.answer("❌ No uploaded service/country found in stock.", show_alert=True)
        return
    if not (state.demo_otp_task and not state.demo_otp_task.done()):
        state.demo_otp_enabled = True
        state.demo_otp_next_at = time.time() + 1
        state.demo_otp_task = asyncio.create_task(demo_otp_monitor(), name="demo_otp_monitor")
    else:
        state.demo_otp_enabled = True
    await c.answer("🧪 DEMO OTP started.")
    await safe_edit(c.message, demo_otp_admin_text(), demo_otp_admin_keyboard())


@dp.callback_query(F.data == "adm_demo_otp_stop")
async def adm_demo_otp_stop_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    state.demo_otp_enabled = False
    task = state.demo_otp_task
    state.demo_otp_task = None
    if task and not task.done():
        task.cancel()
    await c.answer("⏹ DEMO OTP stopped.")
    await safe_edit(c.message, demo_otp_admin_text(), demo_otp_admin_keyboard())


async def main():
    global users_db, BOT_USERNAME
    users_db = load_users()
    check_daily_reset()
    print(f"[Users] {len(users_db)} users loaded from users.json")

    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    print(f"[Bot] Username: @{BOT_USERNAME}")
    
    # Initialize SQL database (SMS Hadi & Balance system integration)
    print(f"[Database] Data directory: {_DATA_DIR}")
    print(f"[Database] SQLite file: {DB_FILE}")
    print(f"[Database] Data directory exists: {os.path.isdir(_DATA_DIR)}")
    await init_db()
    print("[Database] SMS Hadi Stock & Balance SQLite initialized.")

    state.co_admin_ids = await load_co_admins()
    print(f"[Admin] {len(state.co_admin_ids)} co-admin(s) loaded.")
    
    # Run background loops concurrently — one per panel (base + any dynamically added ones,
    # already loaded into SMS_PROVIDERS by load_dynamic_panels() inside init_db()).
    # All configured token+URL panels use the same live monitor.
    # Dynamically added panels are loaded from the admin-managed registry and
    # their messages are checked every second by default.
    state.provider_tasks = {
        p: asyncio.create_task(sms_provider_monitor(p), name=f"{p}_sms_monitor")
        for p in SMS_PROVIDERS
    }

    # Resume admin-created pools that were active before a restart.
    active_pool_ids = await _get_active_forward_pool_ids()
    state.forward_pool_tasks = {
        pool_id: asyncio.create_task(forward_pool_monitor(pool_id), name=f"forward_pool_{pool_id}")
        for pool_id in active_pool_ids
    }
    print(f"[Forward Pools] {len(active_pool_ids)} active pool(s) resumed.")

    # Auto Captcha Panels — already loaded into CAPTCHA_PANELS by load_captcha_panels()
    # inside init_db(); spawn one monitor loop per panel, parallel to the above.
    state.captcha_panel_tasks = {
        key: asyncio.create_task(captcha_panel_monitor(key), name=f"{key}_captcha_monitor")
        for key in CAPTCHA_PANELS
    }
    print(f"[Auto Captcha Panel] {len(CAPTCHA_PANELS)} panel(s) loaded.")

    state.deleted_cleanup_task = asyncio.create_task(
        deleted_account_cleanup_loop(), name="deleted_account_cleanup"
    )

    print("[DeletedCleanup] Safe deleted-account cleanup task started.")
    print("🚀 Advanced SMS Bot Online — Ready!")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "chat_member"],
            polling_timeout=20,
            handle_signals=True,
            drop_pending_updates=True,
        )
    finally:
        # Close HTTP clients on shutdown/error so aiohttp/httpx resources
        # are not left open.
        cleanup_task = getattr(state, "deleted_cleanup_task", None)
        if cleanup_task and not cleanup_task.done():
            cleanup_task.cancel()
        for task in list(state.provider_tasks.values()) + list(state.forward_pool_tasks.values()) + list(state.captcha_panel_tasks.values()):
            if task and not task.done():
                task.cancel()

        await asyncio.gather(
            *(t for t in list(state.provider_tasks.values()) + list(state.forward_pool_tasks.values()) + list(state.captcha_panel_tasks.values()) if t),
            return_exceptions=True,
        )

        await close_provider_http_clients()

        for client in list(captcha_sessions.values()):
            try:
                await client.aclose()
            except Exception:
                pass
        captcha_sessions.clear()

        try:
            await _tg_client.aclose()
        except Exception:
            pass

        try:
            await bot.session.close()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())
