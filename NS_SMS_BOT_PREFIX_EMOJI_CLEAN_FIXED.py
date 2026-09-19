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

# ================= CONFIGURATION ==================
BOT_TOKEN        = "8903349571:AAGl9ERQT3YZYD7lx0Pw3een3zNqpow0rbc"
CHANNEL_ID       = "@nonstopxearn"
OTP_GROUP        = "@nssmsotp"
FORWARD_GROUP_ID = 1004474161960
SUPPORT_USERNAME = "@ns999x"   # User support contact
ADMIN_ID         = 8385712100          # Admin Telegram User ID (super admin — cannot be removed)
MAX_CO_ADMINS    = 3                   # how many co-admins can be added besides the super admin

# SMS Hadi API Database File
DB_FILE = "sms_database.db"

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

# Refer & Earn — % commission credited to referrer on every earning of their referred user
REFERRAL_COMMISSION_RATE = 0.05   # 5%

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

    # (seconds, used while there are active rented numbers). Editable from

    # Force Join System — master on/off switch (admin can flip without
    # deleting the configured channel list).
    "force_join_enabled": "1"}

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
E_OTP_GLOBE       = "5287292843763713628"   # 🌍  Country লাইনে
E_OTP_PHONE       = "5285238101344544669"   # 📱  Number লাইনে
E_OTP_KEY         = "5197288647275071607"   # 🔑  OTP লাইনে

# ── OTP Forward Card (Group এ forward) ────────────────
E_FWD_BELL        = "5458603043203327669"   # 📥  OTP Notification title
E_FWD_OK          = "5237715233107110042"   # ✅  Verified Successfully
E_FWD_SERVICE     = "5247087775164932249"   # 🏷  Service লাইনে
E_FWD_COUNTRY     = "5287292843763713628"   # 🌍  Country লাইনে
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
# Eta shudhu-i OTP Forward Card (FORWARD_GROUP_ID e forward hoya card)-er
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
E_ADMIN_WRENCH    = "6206108815075579644"   # 🔧  Maintenance
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
    """Render Telegram Premium/custom emoji with a Unicode fallback.
    Service emojis remain Premium/custom instead of being replaced globally.
    """
    try:
        eid = str(emoji_id or "").strip()
    except Exception:
        eid = ""
    if eid.isdigit():
        return f'<tg-emoji emoji-id="{eid}">{html.escape(str(fallback))}</tg-emoji>'
    return str(fallback)

def _button_has_unicode_emoji(text: str) -> bool:
    """Return True when a button label already contains a normal Unicode emoji."""
    for ch in str(text):
        cp = ord(ch)
        if (
            0x1F000 <= cp <= 0x1FAFF or
            0x2600 <= cp <= 0x27BF or
            0x2300 <= cp <= 0x23FF or
            0x2B00 <= cp <= 0x2BFF
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
    # Phone-number buttons stay clean; don't add an icon before +244..., +880..., etc.
    if re.fullmatch(r"\+?\d+", raw):
        return ""
    mapping = (
        ("change number", "🔄 "), ("change country", "🌍 "),
        ("prefix", "🔎 "), ("get number", "📱 "), ("otp group", "📩 "),
        ("delete stock", "🗑️ "), ("delete", "🗑️ "), ("stock upload", "📤 "),
        ("upload", "📤 "), ("download", "📥 "), ("withdraw", "💸 "),
        ("referral", "🔗 "), ("profile", "👤 "), ("settings", "⚙️ "),
        ("bot settings", "⚙️ "), ("panel management", "🧩 "),
        ("manage users", "👥 "), ("broadcast", "📣 "), ("service", "🛠️ "),
        ("country", "🌍 "), ("select", "☑️ "), ("stats", "📊 "),
        ("refresh", "🔄 "), ("back", "↩️ "), ("close", "❌ "),
        ("cancel", "❌ "), ("save", "💾 "), ("start", "▶️ "),
        ("stop", "⏹️ "), ("add", "➕ "), ("remove", "➖ "),
        ("clear", "🧹 "), ("maintenance", "🛠️ "),
        ("force join", "🔗 "), ("approve", "✅ "), ("decline", "❌ "),
    )
    for key, emoji in mapping:
        if key in low:
            return emoji
    # No generic blue-diamond marker for unmatched labels.
    return ""


def btn(text: str, emoji_id: str = None, **kwargs) -> dict:
    # Every normal button gets one standard Unicode emoji. Premium/custom
    # emoji are intentionally not used; labels containing "Premium" are
    # left untouched. Existing Unicode emoji are also left untouched.
    text = str(text)
    prefix = _button_emoji(text)
    if prefix:
        text = prefix + text

    # Build a Bot API compatible inline-keyboard button.  Do not send
    # icon_custom_emoji_id: older/current Telegram Bot API deployments can
    # reject that field, which breaks the whole admin keyboard with
    # TelegramBadRequest.  The visible Unicode emoji in `text` is used as
    # the safe fallback instead.
    style = kwargs.pop("style", "primary")
    if style not in {"primary", "success", "danger"}:
        style = "primary"
    d = {"text": text, "style": style}
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
FWD_CHANNEL_URL     = f"https://t.me/{CHANNEL_ID[1:]}"
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
    maintenance_mode   = False
    rk_visible         = set()
    otp_history        = {}
    pending_user_search = {}   # admin uid -> 'ban'|'unban'|'kick'

    # --- Active order state ---
    active_orders        = {}   # legacy/live order compatibility
    service_flow_messages = {}   # user_id -> {chat_id, message_id, service}; keeps only one active service UI
    prefix_filter_pending = {}  # user_id -> {chat_id, message_id, service}
    prefix_filter_active  = {}  # user_id -> {chat_id, message_id, service, prefix, offset}


    # --- SMS Hadi Variables ---
    hadi_settings         = {}
    sent_sms_cache        = set()
    admin_pending_actions = {} # admin uid -> action string
    admin_temp_data       = {} # temp storage during excel upload / add-panel wizard
    provider_tasks        = {} # provider_key -> running asyncio Task (sms_provider_monitor loop)
    captcha_panel_tasks   = {} # captcha panel key -> running asyncio Task (captcha_panel_monitor loop)
    custom_rate_svc_list  = [] # index -> custom service name (for rate-panel callback buttons)
    dlunused_svc_list     = [] # index -> service name (for download-unused-stock callback buttons)
    delstock_countries    = [] # index -> country name (for delete-stock country step)
    delstock_pairs        = [] # index -> (country, service) for direct delete-stock list
    delstock_page         = 0  # current direct stock-list page

    # --- Custom Stock Service → Emoji mapping (persisted in DB) ---
    # custom typed service name (lowercase, e.g. "new fb") -> emoji service
    # key (e.g. "facebook" / "netflix") — admin picks this from a list
    # right after stock upload, so OTP Forward Card shows the right emoji.
    service_emoji_map     = {}
    emoji_pick_options     = [] # index -> emoji service key (for emoji-picker callback buttons)
    
    # --- Balance & Withdrawal System State ---
    pending_withdrawal_amount  = {} # user_id -> True (awaiting amount text input)
    pending_withdrawal_method  = {} # user_id -> amount (awaiting method button click)
    pending_withdrawal_details = {} # user_id -> {"method": str, "amount": float}

    # --- Refer & Earn System State ---
    pending_referrer = {}   # user_id -> referrer_id (captured from /start ref_<id> before verification completes)

    # --- Admin Forward Pool State ---
    # Each pool owns an uploaded number list and polls configured SMS providers
    # on its own interval before forwarding newly-arrived OTPs to the group.
    forward_pool_tasks    = {} # pool_id -> asyncio.Task

    # --- DEMO OTP State ---
    demo_otp_task          = None  # asyncio.Task for admin-controlled demo generator
    demo_otp_enabled       = False
    demo_otp_services      = set()  # selected uploaded services; empty = all uploaded
    demo_otp_countries     = set()  # selected uploaded countries; empty = all uploaded
    demo_otp_next_at       = 0.0   # unix timestamp for next demo OTP batch

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

# ================= MAINTENANCE HELPER =================
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

def is_maintenance(user_id: int) -> bool:
    return state.maintenance_mode and not is_admin(user_id)

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

# ================= USER PERSISTENCE =================
USERS_FILE = "users.json"

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
_SAVE_DEBOUNCE = 30.0

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
        "joined_at":  users_db.get(uid_str, {}).get("joined_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        "last_seen":  time.strftime("%Y-%m-%d %H:%M:%S")}
    state.known_users.add(chat_id)
    if is_new:
        check_daily_reset()
        state.daily_member_count += 1
        save_users(users_db)
    return is_new

users_db: dict = {}

# ================= DATABASE (SMS Hadi Stock & Earnings) =================
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("CREATE TABLE IF NOT EXISTS sent_sms (sms_hash TEXT PRIMARY KEY)")
        await db.execute('''
            CREATE TABLE IF NOT EXISTS numbers (
                phone_number TEXT PRIMARY KEY,
                service TEXT,
                country TEXT,
                status TEXT DEFAULT 'available',
                assigned_to INTEGER DEFAULT NULL,
                assign_time INTEGER DEFAULT NULL,
                otp_rate REAL DEFAULT NULL
            )
        ''')
        # Per-number OTP rate is retained for compatibility, but uploads now always
        # store the single service RATE $ value.
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
        # seeded below from the original hardcoded CHANNEL_ID/OTP_GROUP so
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
                "INSERT OR IGNORE INTO force_join_channels (channel_key, chat_id, label, url, created_at) VALUES (?, ?, ?, ?, ?)",
                ("channel", CHANNEL_ID, "Channel", f"https://t.me/{CHANNEL_ID[1:]}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
        if OTP_GROUP:
            await db.execute(
                "INSERT OR IGNORE INTO force_join_channels (channel_key, chat_id, label, url, created_at) VALUES (?, ?, ?, ?, ?)",
                ("otpgroup", OTP_GROUP, "OTP Group", f"https://t.me/{OTP_GROUP[1:]}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )

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
        await db.commit()

        await db.execute("CREATE INDEX IF NOT EXISTS idx_numbers_status ON numbers(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_numbers_svc_country ON numbers(service, country, status)")

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
# (CHANNEL_ID / OTP_GROUP) — always present, can't be removed from admin
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
        async with db.execute("SELECT channel_key, chat_id, label, url FROM force_join_channels ORDER BY created_at") as cursor:
            rows = await cursor.fetchall()
    state.force_join_channels = {
        key: {"chat_id": chat_id, "label": label, "url": url}
        for key, chat_id, label, url in rows
    }

def sanitize_force_join_key(name: str) -> str:
    """Channel/group নাম থেকে safe, unique key বানায় (শুধু lowercase a-z0-9)।"""
    return re.sub(r"[^a-z0-9]", "", name.strip().lower())

async def add_force_join_channel(key: str, chat_id: str, label: str, url: str):
    """নতুন force-join channel/group DB তে persist করে এবং সাথে সাথে in-memory
    state আপডেট করে দেয় — restart ছাড়াই পরের /start থেকেই check হবে।"""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO force_join_channels (channel_key, chat_id, label, url, created_at) VALUES (?, ?, ?, ?, ?)",
            (key, chat_id, label, url, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()
    state.force_join_channels[key] = {"chat_id": chat_id, "label": label, "url": url}

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
    """Compatibility helper: stock numbers use the same single service rate.

    There is intentionally no separate stock-only rate anymore. The RATE $
    value is the only place where the earning rate is configured.
    """
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
async def get_referrer(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT referrer_id FROM referrals WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_referrer(user_id: int, referrer_id: int) -> bool:
    """একবারই সেট হবে — user_id ইতিমধ্যে referred থাকলে বা নিজেকে refer করলে ignore করবে।"""
    if not referrer_id or referrer_id == user_id:
        return False
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT 1 FROM referrals WHERE user_id = ?", (user_id,)) as cursor:
            if await cursor.fetchone():
                return False
        await db.execute(
            "INSERT INTO referrals (user_id, referrer_id, joined_at) VALUES (?, ?, ?)",
            (user_id, referrer_id, time.strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()
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

def is_refer_notify_enabled() -> bool:
    """Refer Commission notification (inbox SMS to referrer) ON/OFF আছে কিনা।"""
    return state.hadi_settings.get("refer_commission_notify", "on") != "off"

async def set_refer_notify_enabled(enabled: bool):
    val = "on" if enabled else "off"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", ("refer_commission_notify", val))
        await db.commit()
    state.hadi_settings["refer_commission_notify"] = val

async def credit_referral_commission(referred_uid: int, earned_amount: float):
    """referred_uid এর প্রতিটা earning এর 5% তার referrer কে ব্যালেন্স হিসেবে যোগ করে দেয়।"""
    if earned_amount <= 0:
        return
    referrer_id = await get_referrer(referred_uid)
    if not referrer_id:
        return
    commission = round(earned_amount * REFERRAL_COMMISSION_RATE, 2)
    if commission <= 0:
        return
    await add_balance(referrer_id, commission)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO referral_earnings (referrer_id, from_user_id, amount, timestamp) VALUES (?, ?, ?, ?)",
            (referrer_id, referred_uid, commission, time.strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()
    if is_refer_notify_enabled():
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
        return "🌐"
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
    return "🌐"

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
    return f"{ce(E_FLAG_GLOBAL, '🌐')} Global"

def get_country_parts(num):
    key = _parse_country_cached(re.sub(r'\D', '', str(num)))
    if key:
        name, flag_id = COUNTRIES[key]
        short = _prefix_to_short.get(key, name.split('/')[0][:2].upper())
        return ce(flag_id, '🏳'), short
    return ce(E_FLAG_GLOBAL, '🌐'), "GL"

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
        rows.append([btn(f"{mark}{label}", E_FWD_LANG_TAG, callback_data=f"fpool_lang_{code}", style="success" if code in selected_set else "primary")])
    rows.append([btn("Done", E_FWD_OK, callback_data="fpool_lang_done", style="success")])
    rows.append([btn("Cancel", E_BROADCAST_FAIL, callback_data="admin_panel", style="danger")])
    return markup(*rows)

def forward_pool_text(number: str, country: str, service: str, message: str, otp: str, languages: list[str]) -> str:
    """Build the same masked OTP card as the regular forwarder.

    One selected language is used per card. The generator rotates through the
    admin-selected language list between cards so a card never contains a
    stack of repeated translations.
    """
    flag_emoji, short_code = get_country_parts(number)
    fwd_icon = fwd_svc_icon(service)
    selected = [code for code in languages if code in FORWARD_POOL_TRANSLATIONS] or ["en"]
    code = selected[0]
    language_name = FORWARD_POOL_LANGUAGE_LABELS[code]
    return (
        f"{fwd_icon} | {flag_emoji} <b>{short_code}</b> "
        f"<b>{hide_number(number)}</b> | {lang_tag(language_name)}\n\n"
        f"{html.escape(str(message))}"
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

def join_verify_keyboard() -> dict:
    # Users no longer need to press a Verify button.
    # Access is granted automatically as soon as all required channels/groups
    # are joined; the chat_member handler performs the same authoritative check.
    rows = [
        [btn(f"Join {ch['label']}", E_JOIN_CHANNEL, url=ch["url"], style="primary")]
        for ch in state.force_join_channels.values()
    ]
    return markup(*rows)

# ================= TELEGRAM CLIENTS =================
# Must be initialized before any @dp.message / @dp.callback_query decorators.
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
BOT_USERNAME = ""   # filled in main() via bot.get_me()
dp = Dispatcher()

# ================= INSTANT FORCE-JOIN LEAVE DETECTION =================
def _force_join_chat_matches(update: types.ChatMemberUpdated, configured_chat_id: str) -> bool:
    """Match a membership update against a configured @username or numeric chat ID."""
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

    uid = update.from_user.id
    if update.from_user.is_bot:
        return

    matched = any(
        _force_join_chat_matches(update, ch["chat_id"])
        for ch in state.force_join_channels.values()
    )
    if not matched:
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
            await bot.send_message(
                uid,
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Force Join required</b>\n\n"
                "আপনি একটি required channel/group থেকে বের হয়ে গেছেন। "
                "আবার সবগুলোতে join না করা পর্যন্ত bot ব্যবহার করা যাবে না।",
                reply_markup=join_verify_keyboard(),
            )
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
            state.rk_visible.add(uid)
            # All required channels are now joined: remove the old force-join
            # message so its Join buttons do not remain visible in the chat.
            old_join_message_id = state.force_join_messages.pop(uid, None)
            if old_join_message_id:
                try:
                    await bot.delete_message(uid, old_join_message_id)
                except Exception as e:
                    print(f"[ForceJoin] Could not delete join buttons for {uid}: {e}")
            try:
                await send_with_reply_kb(uid, main_menu_text(update.from_user.first_name), uid)
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
                    f"{ce(E_BROADCAST_FAIL, '❌')} <b>Force Join required</b>\n\n"
                    "Please join all required channels/groups. Access will be enabled automatically.",
                    reply_markup=join_verify_keyboard(),
                )
                return
        state.verified_users.add(uid)
        state.rk_visible.add(uid)

    if is_maintenance(uid):
        await m.answer(f"{ce(E_ADMIN_WRENCH, '🔧')} Under maintenance.")
        return
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
        [btn("💸 Withdraw", E_ADMIN_CASH, callback_data="profile_withdraw", style="success")],
        [btn("👥 Referral", E_FWD_OK, copy_text=ref_link, style="primary")],
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
        lines.append(f"• <b>{html.escape(svc)}</b> — <b>{rate:.2f} TK/OTP</b> · Received: <b>{count}</b>")
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


async def show_user_profile(m: types.Message):
    uid = m.from_user.id
    balance, ref_count, total_earned, otp_stats, otp_periods = await asyncio.gather(
        get_balance(uid), count_referrals(uid), get_referral_total_earned(uid),
        get_user_otp_stats(uid), get_user_otp_period_counts(uid)
    )
    today_otp, seven_day_otp, thirty_day_otp, all_time_otp = otp_periods
    text = (
        "👤 <b>Profile</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"💬Today OTP: <code>{today_otp}</code>\n\n"
        f"💬7 Day OTP: <code>{seven_day_otp}</code>\n\n"
        f"💬30 Day OTP: <code>{thirty_day_otp}</code>\n\n"
        f"💬All Time OTP: <code>{all_time_otp}</code>\n\n"
        f"👥 Total Refer: <code>{ref_count}</code>\n\n"
        f"🎁 Refer income: <code>{total_earned:.2f} TK</code>\n\n"
        f"💰Total Balance: <code>{balance:.2f} TK</code>"
    )
    sent = await m.answer(text, reply_markup=profile_keyboard(uid), parse_mode="HTML")
    # Track profile so pressing Get Number removes this screen.
    _remember_service_flow(uid, sent, "profile")


@dp.callback_query(F.data == "profile_balance")
async def profile_balance(c: types.CallbackQuery):
    uid = c.from_user.id
    balance = await get_balance(uid)
    await c.answer()
    await safe_edit(
        c.message,
        "💰 <b>Your Balance</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💵 Available Balance: <b>{balance:.2f} TK</b>\n"
        f"💵 Minimum Withdrawal: <b>{get_min_withdraw():.2f} TK</b>",
        markup(
            [btn("💸 Withdraw", E_ADMIN_CASH, callback_data="profile_withdraw", style="success")],
            [btn("↩️ Back to Profile", E_TOOL_BACKBUTTON, callback_data="profile_back", style="primary")],
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
        "👥 <b>Refer & Earn</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{service_block}"
        f"👥 Referrals: <b>{ref_count}</b>\n"
        f"💰 Total Earned: <b>{total_earned:.2f} TK</b>\n"
        f"📈 Commission: <b>{REFERRAL_COMMISSION_RATE * 100:.0f}%</b>\n\n"
        "🔗 Your Referral Link:\n"
        f"<code>{html.escape(ref_link)}</code>"
    )
    await c.answer()
    await safe_edit(
        c.message, text,
        markup(
            [btn("📤 Share Link", E_BROADCAST, url=f"https://t.me/share/url?url={ref_link}", style="primary")],
            [btn("↩️ Back to Profile", E_TOOL_BACKBUTTON, callback_data="profile_back", style="primary")],
        )
    )


@dp.callback_query(F.data == "profile_back")
async def profile_back(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    balance, ref_count, total_earned, otp_periods = await asyncio.gather(
        get_balance(uid), count_referrals(uid), get_referral_total_earned(uid),
        get_user_otp_period_counts(uid)
    )
    today_otp, seven_day_otp, thirty_day_otp, all_time_otp = otp_periods
    text = (
        "👤 <b>Profile</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"💬Today OTP: <code>{today_otp}</code>\n\n"
        f"💬7 Day OTP: <code>{seven_day_otp}</code>\n\n"
        f"💬30 Day OTP: <code>{thirty_day_otp}</code>\n\n"
        f"💬All Time OTP: <code>{all_time_otp}</code>\n\n"
        f"👥 Total Refer: <code>{ref_count}</code>\n\n"
        f"🎁 Refer income: <code>{total_earned:.2f} TK</code>\n\n"
        f"💰Total Balance: <code>{balance:.2f} TK</code>"
    )
    await safe_edit(c.message, text, profile_keyboard(uid))


@dp.callback_query(F.data == "profile_withdraw")
async def profile_withdraw(c: types.CallbackQuery):
    uid = c.from_user.id
    bal = await get_balance(uid)
    min_wd = get_min_withdraw()
    if bal < min_wd:
        await c.answer(f"⚠️ Minimum withdrawal is {min_wd:.2f} TK!", show_alert=True)
        return
    await c.answer()
    state.pending_withdrawal_amount[uid] = True
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
        "💸 <b>ENTER AMOUNT TO WITHDRAW:</b>"
    )
    await safe_edit(
        c.message, text,
        markup([btn("CANCEL", E_BROADCAST_FAIL, callback_data="profile_back", style="danger")])
    )


def main_reply_keyboard(user_id: int = 0) -> dict:
    # Telegram Bot API reply-keyboard buttons support native styles:
    # danger = red, success = green, primary = blue.
    # Do NOT send icon_custom_emoji_id here; that was the source of the
    # previous TelegramBadRequest on some deployments.
    rows = [
        [
            {"text": "📞 Get Number", "style": "danger"},
            {"text": "👤 Profile", "style": "success"},
        ],
        [{"text": "💬 Support", "style": "primary"}],
    ]
    if is_admin(user_id):
        # Telegram's official Bot API currently has no native yellow/warning
        # button style. Keep this valid with primary/blue and the yellow gear.
        rows.append([{"text": "⚙️ Admin Panel", "style": "primary"}])
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


def main_menu_text(name: str) -> str:
    # Main welcome screen: show only the bot welcome text, without the user's name.
    return "👋 <b>WELCOME TO NS SMS BOT</b> 👋"

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

    return (
        f"{ce(E_ADMIN_TOOL, '⚙️')} <b>NS SMS BOT — Admin Panel</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{ce(E_ADMIN_OTP, '📲')} <b>Today OTP:</b> <code>{state.daily_otp_count}</code>\n"
        f"{ce(E_ADMIN_OTP, '📲')} <b>7 Day OTP:</b> <code>{otp_7_day}</code>\n"
        f"{ce(E_ADMIN_OTP, '📲')} <b>30 Day OTP:</b> <code>{otp_30_day}</code>\n"
        f"{ce(E_ADMIN_OTP, '📲')} <b>LIFETIME OTP:</b> <code>{otp_lifetime}</code>"
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

    selected_services = {str(x).strip() for x in getattr(state, 'demo_otp_services', set()) if str(x).strip()}
    selected_countries = {str(x).strip() for x in getattr(state, 'demo_otp_countries', set()) if str(x).strip()}
    options, seen = [], set()
    for service, country, source_number in rows:
        service = str(service).strip(); country = str(country).strip()
        if selected_services and service not in selected_services:
            continue
        if selected_countries and country not in selected_countries:
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
    if flag_emoji == "🌐":
        digits = re.sub(r"\D", "", display_number)
        flag_emoji, _short = get_country_parts(digits)
        # get_country_parts may return a custom emoji; keep a visible fallback.
        if not flag_emoji:
            flag_emoji = "🌐"
    return (
        f"📱 <b>{html.escape(display_number)}</b>\n"
        f"{svc_tag_icon(service)} <b>{html.escape(svc_display_name(service))}</b> · "
        f"{flag_emoji} <b>{html.escape(country)}</b>"
    )


def _demo_keyboard(otp: str, service: str = None, country: str = None) -> dict:
    # OTP is visible ONLY on the green copy button.
    rows = [[btn(otp, E_OTP_KEY, copy_text=otp, style="success")]]
    # Use the same Get Number button/code as the original forward OTP cards.
    # It opens the normal Get Number flow via the configured URL.
    rows.append([btn(bold_button("Get Number"), E_RK_GET_NUM,
                      url=FWD_GET_NUMBER_URL, style="primary")])
    return markup(*rows)


async def demo_otp_monitor():
    """Send 1-3 synthetic DEMO OTP cards every 1-90 seconds.

    Service/country choices come exclusively from currently uploaded stock metadata.
    No stock number is allocated, consumed, or sent to the group.
    """
    tag = "[DEMO OTP]"
    print(f"{tag} generator started.")
    while state.demo_otp_enabled:
        try:
            options = await _demo_uploaded_options()
            if not options:
                print(f"{tag} no uploaded service/country available; waiting 10s.")
                await asyncio.sleep(10)
                continue

            batch_size = secrets.randbelow(3) + 1
            for _ in range(batch_size):
                service, country, cc = secrets.choice(options)
                local2 = f"{secrets.randbelow(90) + 10:02d}"
                last4 = f"{secrets.randbelow(10000):04d}"
                otp = f"{secrets.randbelow(1000000):06d}"
                display_number = _demo_masked_number(cc, local2, last4)
                text = _demo_card_text(service, country, display_number)
                # DEMO OTP goes to the dedicated OTP group first.
                # Keep the normal forward-group setting as a fallback.
                demo_target = OTP_GROUP or FORWARD_GROUP_ID
                try:
                    await bot.send_message(
                        chat_id=demo_target,
                        text=text,
                        parse_mode="HTML",
                        reply_markup=_demo_keyboard(otp, service, country),
                    )
                    print(f"{tag} sent to {demo_target}")
                except Exception as send_err:
                    print(f"{tag} send error to {demo_target}: {send_err}")
                # Small random spacing inside a batch too, so 1-3 cards do not
                # always arrive as one identical burst.
                if _ > 0:
                    await asyncio.sleep(secrets.randbelow(6) + 1)

            delay = secrets.randbelow(90) + 1
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
        [btn("Service", E_DEMO_OTP, callback_data="adm_demo_otp_select", style="primary")],
        [btn("Start" if not running else "Stop", E_DEMO_OTP,
             callback_data="adm_demo_otp_start" if not running else "adm_demo_otp_stop",
             style="success" if not running else "danger")],
        [btn("Refresh", E_TOOL_REFRESHING, callback_data="adm_demo_otp", style="primary")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")],
    )


def demo_otp_admin_text() -> str:
    """Compact DEMO OTP screen: only the next-OTP countdown is shown."""
    running = bool(state.demo_otp_task and not state.demo_otp_task.done() and state.demo_otp_enabled)
    if running and state.demo_otp_next_at:
        remaining = max(0, int(state.demo_otp_next_at - time.time()))
        countdown = f"{remaining} seconds"
    else:
        countdown = "Stopped"
    return f"⏳ <b>Next OTP: {html.escape(countdown)}</b>"


def demo_otp_selector_keyboard(pairs) -> dict:
    """Show only actually uploaded service/country pairs, then Select."""
    rows = []
    pairs = sorted({(str(service).strip(), str(country).strip())
                    for service, country in pairs
                    if str(service).strip() and str(country).strip()},
                   key=lambda x: (x[0].lower(), x[1].lower()))
    for i, (service, country) in enumerate(pairs):
        selected = service in state.demo_otp_services or country in state.demo_otp_countries
        label = ("✅ " if selected else "⬜ ") + f"{service} — {country}"
        rows.append([btn(label, E_DEMO_OTP, callback_data=f"adm_demo_pair_{i}",
                         style="success" if selected else "primary")])
    rows.append([btn("Select", E_DEMO_OTP, callback_data="adm_demo_select_confirm", style="success")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_demo_otp", style="primary")])
    return markup(*rows)


def admin_keyboard() -> dict:
    # Main admin menu: RATE $ is the single source of truth for all earning rates,
    # including locally uploaded stock numbers.
    return markup(
        [btn("📁 Stock Upload", E_RK_GET_NUM, callback_data="adm_upload_stock", style="success")],
        [btn("🗑️ Delete Stock", E_BROADCAST_FAIL, callback_data="adm_delete_stock", style="danger"),
         btn("📄 Bot Settings", E_ADMIN_WRENCH, callback_data="adm_settings", style="primary")],
        [btn("👥 Manage Users", E_ADMIN_USERS, callback_data="adm_users", style="primary")],
        [btn("📣 Broadcast", E_BROADCAST, callback_data="adm_broadcast_menu", style="success")],
        [btn("🧪 DEMO OTP", E_DEMO_OTP, callback_data="adm_demo_otp", style="success")],
    )

def admin_cat_overview_keyboard() -> dict:
    return markup(
        [btn("Stats",           E_ADMIN_DATE, callback_data="adm_stats",    style="success")],
        [btn("↩️ Back",            E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")],
    )

def admin_cat_control_keyboard() -> dict:
    # Bot Control submenu. Keep these callbacks aligned with the handlers
    # already present in this file.
    return markup(
        [btn("Maintenance", E_ADMIN_WRENCH, callback_data="adm_maintenance", style="danger")],
        [btn("Panel Management", E_ADMIN_TOOL, callback_data="adm_panel_mgmt", style="success")],
        [btn("Force join", E_JOIN_CHANNEL, callback_data="adm_forcejoin_mgmt", style="success")],
        [btn("Clear OTP", E_BROADCAST_FAIL, callback_data="adm_clear_otp_history", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")],
    )


def admin_cat_users_keyboard() -> dict:
    return markup(
        [btn("Users",           E_ADMIN_USERS, callback_data="adm_users",     style="success"),
         btn("Co-Admins",       E_ADMIN_USERS, callback_data="adm_co_admins", style="success")],
        [btn("↩️ Back",            E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")],
    )

def admin_cat_earnings_keyboard() -> dict:
    return markup(
        [btn("Rate",   E_ADMIN_CASH, callback_data="adm_rates",          style="success"),
         btn("Broadcast",       E_BROADCAST,  callback_data="adm_broadcast_menu", style="success")],
        [btn("↩️ Back",            E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")],
    )

def admin_cat_stock_keyboard() -> dict:
    return markup(
        [btn("Upload Stock",    E_RK_GET_NUM,   callback_data="adm_upload_stock",    style="success"),
         btn("Number Forward Pool", E_ADMIN_BOLT, callback_data="adm_forward_pool_upload", style="success")],
        [btn("Active Forward Pools", E_ADMIN_OTP, callback_data="adm_forward_pool_list", style="primary")],
        [btn("Delete Stock",    E_ADMIN_WRENCH, callback_data="adm_delete_stock",    style="success")],
        [btn("Download Unused", E_ADMIN_BOLT,   callback_data="adm_download_unused", style="success"),
         btn("Backup users.json", E_ADMIN_USERS, callback_data="adm_backup_users",   style="success")],
        [btn("↩️ Back",            E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")],
    )


def admin_stats_keyboard() -> dict:
    return markup(
        [btn("Refresh Stats", E_TOOL_REFRESHING, callback_data="adm_stats",    style="success")],
        [btn("↩️ Back",          E_TOOL_BACKBUTTON, callback_data="admin_panel",  style="primary")],
    )

def admin_maintenance_keyboard() -> dict:
    mode_text = "Turn OFF Maintenance" if state.maintenance_mode else "Turn ON Maintenance"
    return markup(
        [btn(mode_text,    E_ADMIN_WRENCH,    callback_data="toggle_maintenance",  style="danger")],
        [btn("↩️ Back",       E_TOOL_BACKBUTTON, callback_data="admin_panel",         style="primary")],
    )

def admin_co_admins_text() -> str:
    lines = [f"{ce(E_ADMIN_USERS, '👥')} <b>Co-Admin Management</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
    lines.append(f"{ce(E_FWD_OK, '👑')} Super Admin: <code>{ADMIN_ID}</code>\n")
    if state.co_admin_ids:
        for i, cid in enumerate(sorted(state.co_admin_ids), 1):
            lines.append(f"{i}. <code>{cid}</code>")
    else:
        lines.append("<i>No co-admins added yet.</i>")
    lines.append(f"\n{ce(E_MENU_PIN, '📌')} <b>{len(state.co_admin_ids)}/{MAX_CO_ADMINS}</b> co-admin slots used")
    return "\n".join(lines)

def admin_co_admins_keyboard() -> dict:
    rows = []
    if len(state.co_admin_ids) < MAX_CO_ADMINS:
        rows.append([btn("Add Co-Admin", E_FWD_OK, callback_data="adm_addco", style="success")])
    for cid in sorted(state.co_admin_ids):
        rows.append([btn(f"Remove {cid}", E_BROADCAST_FAIL, callback_data=f"adm_rmco_{cid}", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])
    return markup(*rows)

def admin_broadcast_keyboard() -> dict:
    return markup(
        [btn("Send Broadcast",  E_BROADCAST,       callback_data="admin_broadcast",  style="danger")],
        [btn("↩️ Back",            E_TOOL_BACKBUTTON,  callback_data="admin_panel",      style="primary")],
    )

def admin_users_keyboard(page: int = 0, total: int = 0) -> dict:
    rows = [
        [btn("View All Users",    E_ADMIN_USERS,     callback_data="adm_users_list_0",  style="primary")],
        [btn("Search / Ban User", E_ADMIN_WRENCH,    callback_data="adm_user_ban",       style="danger")],
        [btn("Unban User",        E_FWD_OK,          callback_data="adm_user_unban",     style="success")],
        [btn("User Stats",        E_ADMIN_DATE,      callback_data="adm_user_stats",     style="primary")],
        [btn("Add Balance",       E_ADMIN_CASH,      callback_data="adm_add_balance",    style="success")],
        [btn("Download User Data", E_ADMIN_USERS,     callback_data="adm_backup_users",    style="success")],
        [btn("Upload User Data",   E_ADMIN_USERS,     callback_data="adm_restore_users",   style="primary")],
        [btn("↩️ Back",              E_TOOL_BACKBUTTON, callback_data="admin_panel",        style="primary")],
    ]
    return {"inline_keyboard": rows}

def admin_settings_keyboard() -> dict:
    # Compact action buttons; Back stays large/full-width.
    rows = [
        [
            btn("Clear OTP", E_BROADCAST_FAIL, callback_data="adm_clear_otp_history", style="danger"),
            btn("Force join", E_JOIN_CHANNEL, callback_data="adm_forcejoin_mgmt", style="success"),
        ],
        [
            btn("All Panel", E_ADMIN_TOOL, callback_data="adm_panel_mgmt", style="success"),
        ],
        [
            btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary"),
        ],
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
            style="success" if configured else "danger",
        ))
    # compact two-column layout
    for i in range(0, len(panel_buttons), 2):
        rows.append(panel_buttons[i:i+2])
    rows.append([
        btn("Add Panel", E_FWD_OK, callback_data="adm_add_panel", style="success"),
    ])
    # Keep the separate login-based panel types accessible from Panel Management.
    rows.append([
        btn("🔐 Auto Captcha Panel", E_MENU_LOCK, callback_data="adm_captcha_mgmt", style="success"),
    ])
    rows.append([
        btn("🟢 Green SMS Panel", E_MENU_LOCK, callback_data="adm_green_mgmt", style="success"),
    ])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_settings", style="primary")])
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
        panel_buttons.append(btn(f"{dot} {label}", E_MENU_LOCK, callback_data=f"adm_cap_detail_{key}", style="success" if ok else "danger"))
    for i in range(0, len(panel_buttons), 2):
        rows.append(panel_buttons[i:i + 2])
    rows.append([btn("➕ Add Auto Captcha Panel", E_FWD_OK, callback_data="adm_add_cap_panel", style="success")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="primary")])
    return markup(*rows)

def captcha_panel_detail_keyboard(key: str) -> dict:
    rows = [
        [btn("🔁 Retry Login", E_TOOL_REFRESHING, callback_data=f"adm_cap_retry_{key}", style="primary")],
        [btn("🧪 Test Connection", E_FWD_OK, callback_data=f"adm_cap_test_{key}", style="success")],
        [btn("🗑 Remove Panel", E_BROADCAST_FAIL, callback_data=f"adm_rm_cap_panel_{key}", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")],
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
        panel_buttons.append(btn(f"{dot} {label}", E_MENU_LOCK, callback_data=f"adm_green_detail_{key}", style="success" if ok else "danger"))
    for i in range(0, len(panel_buttons), 2):
        rows.append(panel_buttons[i:i + 2])
    rows.append([btn("➕ Add Green Panel", E_FWD_OK, callback_data="adm_add_green_panel", style="success")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="primary")])
    return markup(*rows)

def green_panel_detail_keyboard(key: str) -> dict:
    rows = [
        [btn("🔁 Retry Login", E_TOOL_REFRESHING, callback_data=f"adm_green_retry_{key}", style="primary")],
        [btn("🧪 Test Connection", E_FWD_OK, callback_data=f"adm_green_test_{key}", style="success")],
        [btn("🗑 Remove Panel", E_BROADCAST_FAIL, callback_data=f"adm_rm_green_panel_{key}", style="danger")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="primary")],
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
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="primary")])
    return markup(*rows)

def force_join_mgmt_keyboard() -> dict:
    """Compact Force Join controls: two small green channel buttons per row."""
    rows = []
    ch_buttons = []
    for key, ch in state.force_join_channels.items():
        username = ch.get("chat_id", "")
        display = username if isinstance(username, str) and username.startswith("@") else ch.get("label", key)
        ch_buttons.append(btn(f"📢 {display}", E_JOIN_CHANNEL, callback_data=f"adm_fj_detail_{key}", style="success"))
    for i in range(0, len(ch_buttons), 2):
        rows.append(ch_buttons[i:i + 2])
    rows.append([btn("➕ Add", E_FWD_OK, callback_data="adm_fj_add", style="success")])
    enabled = force_join_enabled()
    rows.append([btn(f"Force Join: {'🟢 ON' if enabled else '🔴 OFF'}", E_TOOL_REFRESHING, callback_data="adm_fj_toggle", style="success" if enabled else "danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_settings", style="primary")])
    return markup(*rows)

def force_join_detail_keyboard(key: str) -> dict:
    rows = [[btn("✏️ Edit URL", E_MENU_GLOBE2, callback_data=f"adm_fj_seturl_{key}", style="danger")]]
    if key not in BASE_FORCE_JOIN_KEYS:
        rows.append([btn("🗑 Remove", E_BROADCAST_FAIL, callback_data=f"adm_fj_rm_{key}", style="danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="primary")])
    return markup(*rows)

async def admin_rates_keyboard() -> dict:
    rows = []
    service_row = []

    # Show the current rate beside every service, with two services per row.
    # All RATE_SERVICES remain visible and each button opens its rate editor.
    for svc in RATE_SERVICES:
        rate = get_service_rate(svc)
        style = "success" if rate > 0 else "danger"
        rate_label = f"{rate:.2f} TK"
        service_row.append(
            btn(
                f"{svc_display_name(svc)} — {rate_label}",
                E_ADMIN_CASH,
                callback_data=f"adm_set_rate_{svc}",
                style=style,
            )
        )
        if len(service_row) == 2:
            rows.append(service_row)
            service_row = []
    if service_row:
        rows.append(service_row)

    # ── Custom (manually added) services ──
    custom_services = await get_custom_rate_services()
    state.custom_rate_svc_list = custom_services
    custom_row = []
    for idx, svc in enumerate(custom_services):
        rate = get_service_rate(svc)
        style = "success" if rate > 0 else "danger"
        rate_label = f"{rate:.2f} TK"
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

    rows.append([btn("➕ Add", E_FWD_OK, callback_data="adm_add_earning_service", style="success")])
    # No separate Stock Number Rates menu: all stock uses RATE $.
    rows.append([btn("🗑 Remove", E_BROADCAST_FAIL, callback_data="adm_remove_earning_service", style="danger")])
    rows.append([btn("Minimum Withdraw", E_ADMIN_CASH, callback_data="adm_set_min_withdraw", style="primary")])

    notify_on = is_refer_notify_enabled()
    notify_label = f"Refer Commission Notify — {'🟢 ON' if notify_on else '🔴 OFF'}"
    rows.append([btn(notify_label, E_RK_REFER_EARN, callback_data="toggle_refer_notify", style="success" if notify_on else "danger")])

    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])
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
_MAIN_SVC_ICON = {k: ce(v, _MAIN_SVC_FALLBACK[k]) for k, v in _MAIN_SVC_EMOJI_ID.items()}
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
        return ce(CUSTOM_SERVICE_EMOJIS[key], "🔷")
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
    start = page * per_page
    chunk = opts[start:start + per_page]

    rows = []
    for idx, (key, label) in enumerate(chunk, start=start):
        rows.append([btn(label, _emoji_id_for_key(key), callback_data=f"svcemoji_pick_{idx}", style="primary")])

    nav_row = []
    if page > 0:
        nav_row.append(btn("Prev", E_TOOL_BACKBUTTON, callback_data=f"svcemoji_pg_{page-1}", style="primary"))
    if start + per_page < total:
        nav_row.append(btn("Next", E_TOOL_REFRESHING, callback_data=f"svcemoji_pg_{page+1}", style="primary"))
    if nav_row:
        rows.append(nav_row)

    rows.append([btn("✏️ Type Service Name", E_ADMIN_TOOL, callback_data="svcemoji_typemode", style="success")])
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
        return ce(CUSTOM_SERVICE_EMOJIS[key], "🔷")
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
        return ce(FORWARD_CARD_SERVICE_EMOJIS[key], "🔷")
    return ce(E_SVC_DEFAULT, "🔷")

def fwd_svc_icon(svc):
    """OTP Forward Card (FORWARD_GROUP_ID e jaowa card)-e use korar
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
    target_chat = FORWARD_GROUP_ID or OTP_GROUP
    if not target_chat:
        print(f"[ForwardPool {pool_id}] No FORWARD_GROUP_ID or OTP_GROUP configured; skipped send.")
        return
    try:
        languages = json.loads(languages_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        languages = ["en"]
    if display_languages:
        languages = display_languages
    text = forward_pool_text(number, country, service, message, otp, languages)
    rows = [[btn(bold_button(str(otp)), E_FWD_OTP, copy_text=str(otp), style="success")]]
    if CHANNEL_ID:
        rows.insert(0, [btn(bold_button("Channel"), E_FWD_BELL, url=FWD_CHANNEL_URL, style="primary")])
    rows.append([btn(bold_button("Get Number"), E_RK_GET_NUM, url=FWD_GET_NUMBER_URL, style="primary")])
    try:
        await bot.send_message(
            chat_id=target_chat,
            text=text,
            parse_mode="HTML",
            reply_markup=markup(*rows),
        )
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
    """Get the first non-empty value from a provider SMS record."""
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip() != "":
            return str(value)
    return ""


def _extract_provider_sms_rows(payload):
    """Normalize common Lamix/Viewstats response shapes into a list of dict rows.

    The original monitor only accepted {status:'success', data:[...]}.  In
    practice panels may return data/records/messages/results/items/rows or a
    single record, and field names may vary.  This helper keeps the rest of
    the forwarding pipeline unchanged while making the API boundary tolerant.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    # If the object itself looks like one SMS record, accept it.
    number_keys = {"num", "number", "phone", "mobile", "msisdn", "to", "receiver", "destination"}
    message_keys = {"message", "msg", "sms", "body", "content", "text"}
    if any(k in payload for k in number_keys) and any(k in payload for k in message_keys):
        return [payload]

    preferred = ("data", "records", "messages", "results", "items", "rows", "sms")
    for key in preferred:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_provider_sms_rows(value)
            if nested:
                return nested
    return []


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
            params = {"token": get_provider_token(provider), "records": 100}
            client = await get_provider_http_client(provider)
            response = await client.get(_provider_request_url(provider), params=params)
            
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
                try:
                    data = response.json()
                except Exception as json_err:
                    print(f"{tag} API returned non-JSON response: {json_err}")
                    data = None

                if _provider_response_is_usable(data):
                    sms_list = _extract_provider_sms_rows(data)
                    if not sms_list:
                        # Valid response but no SMS rows is normal; do not spam logs.
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
                        if len(entries) <= seen_count:
                            continue  # kono notun SMS ashe ni ei number e

                        for sms in entries[seen_count:]:
                            raw_num, msg = _provider_sms_fields(sms)
                            num = raw_num or clean_num
                                
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
                            
                            check_daily_reset()
                            state.daily_otp_count += 1
                            
                            # ---- Credit Balance if Eligible (Stock-only rate) ----
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
                                "phone":     num,
                                "service":   service.upper(),
                                "otp":       otp_str
                            })
                            await record_user_otp(assigned_user, service, earned)
                            
                            # User notification — reference bot design
                            flag_emoji, short_code = get_country_parts(num)
                            svc_icon = svc_tag_icon(service) or svc_display_name(service)
                            otp_text = (
                                f"{flag_emoji} {svc_icon} <code>{num}</code>  "
                                f"{ce(E_ADMIN_CASH, '💰')} <b><code>{earned:.2f}</code></b>"
                            )
                            otp_markup = markup(
                                [btn(otp_str, E_OTP_KEY, copy_text=otp_str, style="success")],
                            )
                            
                            # Broadcast forward notification to channel/group —
                            # forward card er icon ALADA FORWARD_CARD_SERVICE_EMOJIS
                            # dictionary theke ashe (svc_icon theke independent).
                            fwd_icon = fwd_svc_icon(service)
                            lang = detect_language(msg)
                            masked = hide_number(num)
                            f_text = (
                                f"{fwd_icon} | {flag_emoji} <b>{short_code}</b> "
                                f"<b>{masked}</b> | {lang_tag(lang)}\n\n"
                                f"💬 <b>{html.escape(msg)}</b>"
                            )
                            f_markup = markup(
                                [btn(bold_button("Channel"), E_FWD_BELL, url=FWD_CHANNEL_URL, style="primary"),
                                 btn(bold_button(otp_str), E_FWD_OTP, copy_text=otp_str, style="success")],
                                [btn(bold_button("Get Number"), E_RK_GET_NUM, url=FWD_GET_NUMBER_URL, style="primary")]
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
                                    await _tg_client.post(
                                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                        json={
                                            "chat_id": FORWARD_GROUP_ID,
                                            "text": f_text,
                                            "parse_mode": "HTML",
                                            "reply_markup": f_markup
                                        },
                                        timeout=8.0
                                    )
                                except Exception as err:
                                    print(f"{tag} Forward error: {err}")
                            
                            await asyncio.gather(_send_user(), _send_group())

                        # Ei number er shob notun SMS forward howar por total
                        # count DB te update kore dei, r otp_received=1 mark kori
                        # (permanent retire logic er jonno)
                        async with aiosqlite.connect(DB_FILE) as db:
                            await db.execute(
                                "UPDATE numbers SET sms_seen_count = ?, otp_received = 1 WHERE phone_number = ?",
                                (len(entries), db_phone)
                            )
                            await db.commit()

                            # Immediately update the user's number-card color:
                            # GREEN (fresh) -> RED (OTP received), without adding
                            # any extra status buttons underneath.
                            await _refresh_number_card_after_otp(
                                assigned_user, service, country
                            )
            else:
                print(f"{tag} Viewstats status error code: {response.status_code}")
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

                    flag_emoji, short_code = get_country_parts(clean_num)
                    svc_icon = svc_tag_icon(service) or svc_display_name(service)
                    otp_text = (
                        f"{flag_emoji} {svc_icon} <code>{clean_num}</code>  "
                        f"{ce(E_ADMIN_CASH, '💰')} <b><code>{earned:.2f}</code></b>"
                    )
                    otp_markup = markup([btn(otp_str, E_OTP_KEY, copy_text=otp_str, style="success")])

                    # Forward card er icon ALADA FORWARD_CARD_SERVICE_EMOJIS
                    # dictionary theke ashe (svc_icon theke independent).
                    fwd_icon = fwd_svc_icon(service)
                    lang = detect_language(msg)
                    masked = hide_number(clean_num)
                    f_text = (
                        f"{fwd_icon} | {flag_emoji} <b>{short_code}</b> "
                        f"<b>{masked}</b> | {lang_tag(lang)}\n\n"
                        f"💬 <b>{html.escape(str(msg))}</b>"
                    )
                    f_markup = markup(
                        [btn(bold_button("Channel"), E_FWD_BELL, url=FWD_CHANNEL_URL, style="primary"),
                         btn(bold_button(otp_str), E_FWD_OTP, copy_text=otp_str, style="success")],
                        [btn(bold_button("Get Number"), E_RK_GET_NUM, url=FWD_GET_NUMBER_URL, style="primary")]
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
                            target_chat = FORWARD_GROUP_ID or OTP_GROUP
                            if not target_chat:
                                print(f"{tag} Forward skipped: no FORWARD_GROUP_ID/OTP_GROUP configured")
                                return
                            resp = await _tg_client.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                json={"chat_id": target_chat, "text": f_text, "parse_mode": "HTML", "reply_markup": f_markup},
                                timeout=8.0
                            )
                            if resp.status_code != 200:
                                print(f"{tag} Forward HTTP {resp.status_code}: {resp.text[:300]}")
                            else:
                                try:
                                    data = resp.json()
                                    if not data.get("ok"):
                                        print(f"{tag} Forward Telegram error: {data}")
                                except Exception:
                                    pass
                        except Exception as err:
                            print(f"{tag} Forward error: {err}")

                    await asyncio.gather(_send_user(), _send_group())

                # Ei number er shob notun SMS forward howar por total count DB te
                # update kore dei, r otp_received=1 mark kori (permanent retire logic)
                async with aiosqlite.connect(DB_FILE) as db:
                    await db.execute(
                        "UPDATE numbers SET sms_seen_count = ?, otp_received = 1 WHERE phone_number = ?",
                        (len(entries), clean_num)
                    )
                    await db.commit()

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"{tag} Loop error: {e}")

        await asyncio.sleep(10)

# ================= HANDLERS =================

async def _capture_referral_payload(uid: int, text: str):
    """/start ref_<referrer_id> — referrer_id কে pending রাখে, verification সম্পূর্ণ হলে finalize হবে।"""
    if not text:
        return
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].startswith("ref_"):
        return
    if uid in state.pending_referrer:
        return
    try:
        referrer_id = int(parts[1][4:])
    except ValueError:
        return
    if referrer_id != uid:
        state.pending_referrer[uid] = referrer_id

async def _finalize_referral_if_new(uid: int, is_new: bool):
    if not is_new:
        return
    referrer_id = state.pending_referrer.pop(uid, None)
    if referrer_id:
        await set_referrer(uid, referrer_id)

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    uid = m.from_user.id

    if is_banned(uid):
        await m.answer(f"{ce(E_BROADCAST_FAIL, '❌')} You are banned.")
        return
    if is_maintenance(uid):
        await m.answer(
            f"{ce(E_ADMIN_WRENCH, '🔧')} <b>Under maintenance</b>, back soon!",
            parse_mode="HTML"
        )
        return

    await _capture_referral_payload(uid, m.text)

    if uid in state.verified_users:
        # Never trust stale in-memory verification after restart.
        if await is_joined(uid):
            add_user(users_db, m.from_user, uid)
            state.rk_visible.add(uid)
            await send_with_reply_kb(uid, main_menu_text(m.from_user.first_name), uid)
            return
        state.verified_users.discard(uid)
        state.rk_visible.discard(uid)

    # Force Join off or no channels configured — skip the prompt entirely.
    if not force_join_enabled() or not state.force_join_channels:
        state.verified_users.add(uid)
        is_new = add_user(users_db, m.from_user, uid)
        await _finalize_referral_if_new(uid, is_new)
        state.rk_visible.add(uid)
        await send_with_reply_kb(uid, main_menu_text(m.from_user.first_name), uid)
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

    joined = await is_joined(uid)
    if joined:
        state.verified_users.add(uid)
        is_new = add_user(users_db, m.from_user, uid)
        await _finalize_referral_if_new(uid, is_new)
        state.rk_visible.add(uid)
        # Verification is completed silently; remove the verification message
        # instead of leaving a visible "Verified!" message in the chat.
        state.force_join_messages.pop(uid, None)
        try:
            await join_msg.delete()
        except Exception:
            try:
                await bot.delete_message(uid, join_msg.message_id)
            except Exception:
                pass
        await send_with_reply_kb(uid, main_menu_text(m.from_user.first_name), uid)

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
            service_style = "success"
        elif svc_key == "facebook":
            service_style = "primary"
        elif svc_key in {"instagram", "newfb"}:
            service_style = "success"
        elif svc_key == "discord":
            service_style = "primary"
        else:
            service_style = ("primary", "success", "danger")[sum(ord(ch) for ch in svc_key) % 3]
        buttons.append([btn(normal_service_label(svc_name), icon, callback_data=f"hadi_svc_{svc_name}", style=service_style)])

    if not buttons:
        return None

    buttons.append([btn("Close", E_BROADCAST_FAIL, callback_data="close_menu", style="danger")])
    return {"inline_keyboard": buttons}

@dp.callback_query(F.data == "menu_stock_home")
async def cb_menu_stock_home(c: types.CallbackQuery):
    try:
        await c.answer()
    except Exception:
        pass
    if is_maintenance(c.from_user.id):
        return

    kb = await _stock_menu_inline_kb()
    if not kb:
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} No stock available",
            markup([btn("↩️ Back to Main", E_TOOL_BACKBUTTON, callback_data="menu", style="primary")])
        )
        return
    
    await safe_edit(
        c.message,
        f"{ce(E_SVC_LIST, '📋')} Choose a service",
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
    
    if is_maintenance(c.from_user.id):
        return
    
    svc_name = c.data.split("_", 2)[2]
    uid = c.from_user.id

    # Every service button must behave like Get Number: remove the previous
    # service/number panel first, while keeping the message currently being
    # clicked so the new service screen can reuse it safely.
    await _clear_previous_service_flow(uid, keep_message_id=c.message.message_id)
    _remember_service_flow(uid, c.message, svc_name)

    # Switching service immediately invalidates the previous service lease.
    await _release_other_stock_services(c.message.chat.id, keep_service=svc_name)
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """SELECT country, MAX(COALESCE(otp_rate, ?)) AS display_rate
               FROM numbers
               WHERE service = ? AND status = 'available'
               GROUP BY country
               ORDER BY country""",
            (get_stock_rate(svc_name), svc_name)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} No stock for this service",
            markup(
                [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu_stock_home", style="primary")]
            )
        )
        return
    
    country_rows = []
    for r in rows:
        cname = r[0]
        flag_id = get_flag_id_by_country(cname)
        cname_escaped = cname.replace(" ", "_")
        rate = float(r[1] or get_stock_rate(svc_name))
        if rate <= 0:
            rate = get_stock_rate(svc_name)
        rate_text = f"{rate:.2f} BDT"
        short = get_country_parts(next((str(x[0]) for x in []), ""))[1] if False else (COUNTRIES.get(next((k for k,v in COUNTRIES.items() if v[0].split('/')[0].strip().lower() == str(cname).strip().lower()), ""), ("", ""))[0].split('/')[0] or str(cname)[:2].upper())
        country_rows.append([
            {
                "text": f"{country_flag_emoji(cname)} {short} · {rate_text}",
                "callback_data": f"hadi_ctry_{svc_name}_{cname_escaped}",
                "style": "success"}
        ])
    
    country_rows.append([
        {"text": "Back", "callback_data": "menu_stock_home", "style": "primary"},
    ])
    
    text = (
        f"{ce(E_MENU_GLOBE2, '🌐')} <b>{svc_tag(svc_name)}</b> · {ce(E_RANGE_PHONE, '🔢')} {len(rows)} countries"
    )
    
    await safe_edit(c.message, text, {"inline_keyboard": country_rows})

def build_hadi_number_card(service_name, country_name, phone_nums, display_country, label, cc_removed=False, otp_rate=None):
    # Number card: always show a visible country flag + the COMPLETE country name.
    # Do not show the stock/OTP rate on this user-facing card.
    detected_country = country_name_for_number(phone_nums[0]) if phone_nums else ''
    full_country = detected_country if detected_country and detected_country != 'Global' else str(country_name or display_country or 'Global').strip()
    flag = country_flag_emoji(full_country)
    if flag == '🌐' and phone_nums:
        # Fallback to the number-derived Unicode flag when the uploaded country
        # label is formatted differently from the country map.
        digits = re.sub(r'\D', '', str(phone_nums[0]))
        key = _parse_country_cached(digits) if digits else None
        if key:
            iso = _prefix_to_short.get(key)
            if iso and len(iso) == 2:
                flag = unicode_flag(iso)

    text = (
        f"📮 Service : {svc_display_name(service_name)}\n"
        f"🌐 Country :  {flag} {html.escape(full_country)}\n"
        f"⏳ Waiting for OTP"
    )

    svc_icon_id = get_svc_icon(service_name)
    num_btns = []
    # Keep the number buttons clean: the OTP rate is shown with the service above,
    # not beside each individual number.
    for pn in phone_nums:
        if cc_removed:
            stripped = strip_cc(pn)
            display_num = stripped
            copy_num = stripped
        else:
            display_num = f"+{pn}"
            copy_num = f"+{pn}"

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
                _row = _db.execute(
                    "SELECT status, COALESCE(otp_received, 0) FROM numbers WHERE phone_number = ?",
                    (str(pn),),
                ).fetchone()
            if _row:
                number_status = (_row[0] or "available").strip().lower()
                otp_received = int(_row[1] or 0)
        except Exception:
            number_status = "available"
            otp_received = 0

        # FINAL NUMBER COLOR RULE:
        # FRESH = GREEN, USED (OTP received/retired) = RED.
        # Internal status='busy' is deliberately ignored here because it is
        # only the temporary lease state used by the OTP monitor.
        is_used = (otp_received == 1) or (number_status == "retired")
        number_style = "danger" if is_used else "success"

        # Telegram's native inline-button background style. This changes only
        # the number button itself; no extra status/checker button is created.
        num_btns.append(
            btn(
                display_num,
                svc_icon_id,
                copy_text=copy_num,
                style=number_style,
            )
        )

    # FORCE one number per row. Never combine number buttons horizontally.
    # Telegram receives exactly: [[number1], [number2], [number3]].
    rows = [[item] for item in num_btns]

    cname_escaped = country_name.replace(" ", "_")
    rows.append([btn("Change Number", E_TOOL_CHANGENUMBER, callback_data=f"hadi_chgnum_{service_name}_{cname_escaped}", style="danger")])
    # Country Change + Prefix are intentionally on the same row.
    rows.append([
        btn("Change Country", E_MENU_GLOBE2, callback_data=f"hadi_chgctry_{service_name}", style="primary"),
        btn("Prefix", E_MENU_GLOBE2, callback_data=f"hadi_prefix_{service_name}", style="primary"),
    ])
    rows.append([btn("OTP Group", E_MENU_OTPGROUP, url=f"https://t.me/{OTP_GROUP[1:]}")])
    return text, {"inline_keyboard": rows}

@dp.callback_query(F.data.startswith("hadi_ctry_"))
async def cb_hadi_ctry(c: types.CallbackQuery):
    try:
        await c.answer("Leasing numbers...")
    except Exception:
        pass
        
    if is_maintenance(c.from_user.id):
        return
        
    parts = c.data.split("_", 3)
    if len(parts) < 4:
        return
    service_name = parts[2]
    country_name = parts[3].replace("_", " ")
    chat_id = c.message.chat.id
    
    async with aiosqlite.connect(DB_FILE) as db:
        # Check active lease duplication
        async with db.execute("SELECT phone_number FROM numbers WHERE assigned_to = ? AND status = 'busy' LIMIT 1", (chat_id,)) as cur:
            existing = await cur.fetchone()
        
        if existing:
            async with db.execute("SELECT phone_number, otp_rate FROM numbers WHERE assigned_to = ? AND status = 'busy'", (chat_id,)) as cur2:
                busy_rows = await cur2.fetchall()
                phone_nums = [r[0] for r in busy_rows]
                selected_rate = {r[0]: r[1] for r in busy_rows if r[1] is not None}
        else:
            async with db.execute(
                "SELECT phone_number, otp_rate FROM numbers WHERE service = ? AND country = ? AND status = 'available' AND NOT EXISTS (SELECT 1 FROM number_usage_history h WHERE h.phone_number = numbers.phone_number) ORDER BY RANDOM() LIMIT 3",
                (service_name, country_name)
            ) as cursor:
                rows = await cursor.fetchall()
                
            if not rows:
                await safe_edit(
                    c.message,
                    f"{ce(E_BROADCAST_FAIL, '❌')} <b>No numbers available in stock for this country!</b>",
                    markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data=f"hadi_svc_{service_name}", style="primary")])
                )
                return
                
            phone_nums = [r[0] for r in rows]
            selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
            curr_time = int(time.time())
            for pn in phone_nums:
                await db.execute(
                    "UPDATE numbers SET status = 'busy', assigned_to = ?, assign_time = ?, otp_received = 0, sms_seen_count = 0 WHERE phone_number = ? AND status = 'available'",
                    (chat_id, curr_time, pn)
                )
                await db.execute(
                    "INSERT OR IGNORE INTO number_usage_history (phone_number, first_used_at) VALUES (?, ?)",
                    (pn, curr_time)
                )
            await db.commit()
            
    display_country = get_country(phone_nums[0])
    label = svc_tag(service_name)
    text, kb = build_hadi_number_card(service_name, country_name, phone_nums, display_country, label, otp_rate=selected_rate)
    await safe_edit(c.message, text, kb)

async def _toggle_hadi_cc(c: types.CallbackQuery, cc_removed: bool):
    if is_maintenance(c.from_user.id):
        return

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
        cc_removed=cc_removed, otp_rate=selected_rate
    )
    try:
        await safe_edit(c.message, text, kb)
    except Exception as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            raise

@dp.callback_query(F.data.startswith("hadi_rmcc_"))
async def cb_hadi_rmcc(c: types.CallbackQuery):
    try:
        await c.answer("Removing country code...")
    except Exception:
        pass
    await _toggle_hadi_cc(c, cc_removed=True)

@dp.callback_query(F.data.startswith("hadi_addcc_"))
async def cb_hadi_addcc(c: types.CallbackQuery):
    try:
        await c.answer("Adding country code...")
    except Exception:
        pass
    await _toggle_hadi_cc(c, cc_removed=False)

@dp.callback_query(F.data.startswith("hadi_prefix_") & ~F.data.startswith("hadi_prefix_back_") & ~F.data.startswith("hadi_prefix_more"))
async def cb_hadi_prefix(c: types.CallbackQuery):
    """Ask for a number prefix and remember the current card for the result."""
    try:
        await c.answer()
    except Exception:
        pass

    if is_maintenance(c.from_user.id):
        return

    service_name = c.data.split("_", 2)[2]
    state.prefix_filter_active.pop(c.from_user.id, None)
    state.prefix_filter_pending[c.from_user.id] = {
        "chat_id": c.message.chat.id,
        "message_id": c.message.message_id,
        "service": service_name,
    }

    await safe_edit(
        c.message,
        f"{ce(E_RANGE_PHONE, '🔎')} <b>Prefix Filter</b>\n\n"
        f"Send the starting digits of the number.\n"
        f"Example: <code>88019255</code>\n\n"
        f"Only numbers uploaded for <b>{html.escape(svc_display_name(service_name))}</b> "
        f"that start with this prefix will be shown.",
        markup([
            [btn("↩️ Back", E_TOOL_BACKBUTTON,
                  callback_data=f"hadi_prefix_back_{service_name}", style="primary")]
        ])
    )


@dp.callback_query(F.data.startswith("hadi_prefix_back_"))
async def cb_hadi_prefix_back(c: types.CallbackQuery):
    try:
        await c.answer()
    except Exception:
        pass

    uid = c.from_user.id
    state.prefix_filter_pending.pop(uid, None)
    state.prefix_filter_active.pop(uid, None)
    service_name = c.data.split("_", 3)[3]
    chat_id = c.message.chat.id

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT phone_number, otp_rate FROM numbers "
            "WHERE assigned_to = ? AND service = ? AND status = 'busy' ORDER BY rowid",
            (chat_id, service_name)
        ) as cur:
            rows = await cur.fetchall()

    if rows:
        phone_nums = [r[0] for r in rows]
        selected_rate = {r[0]: r[1] for r in rows if r[1] is not None}
        display_country = get_country(phone_nums[0])
        text, kb = build_hadi_number_card(
            service_name, get_country(phone_nums[0]), phone_nums,
            display_country, svc_tag(service_name), otp_rate=selected_rate
        )
        await safe_edit(c.message, text, kb)
    else:
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} <b>No active number found.</b>",
            markup([btn("↩️ Back", E_TOOL_BACKBUTTON,
                        callback_data=f"hadi_svc_{service_name}", style="primary")])
        )


@dp.callback_query(F.data == "hadi_prefix_more")
async def cb_hadi_prefix_more(c: types.CallbackQuery):
    """Show the next 3 numbers for the active prefix filter.

    There is intentionally no overall result limit. Each press advances by
    exactly three matching stock numbers until the matching stock is exhausted.
    """
    try:
        await c.answer("Loading 3 more numbers...")
    except Exception:
        pass

    if is_maintenance(c.from_user.id):
        return

    uid = c.from_user.id
    active = state.prefix_filter_active.get(uid)
    if not active:
        await c.answer("⚠️ Prefix filter expired. Press Prefix again.", show_alert=True)
        return

    service_name = active["service"]
    prefix = active["prefix"]
    offset = int(active.get("offset", 0))
    chat_id = active["chat_id"]
    message_id = active["message_id"]

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT phone_number, otp_rate FROM numbers "
            "WHERE service = ? AND status = 'available' "
            "AND phone_number LIKE ? "
            "AND NOT EXISTS (SELECT 1 FROM number_usage_history h "
            "                WHERE h.phone_number = numbers.phone_number) "
            "ORDER BY phone_number LIMIT 3 OFFSET ?",
            (service_name, prefix + "%", offset)
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        try:
            await c.answer("⚠️ No more matching numbers available.", show_alert=True)
        except Exception:
            pass
        return

    state.prefix_filter_active[uid]["offset"] = offset + len(rows)

    phone_nums = [r[0] for r in rows]
    display_country = get_country(phone_nums[0]) if phone_nums else ""
    flag = country_flag_emoji(display_country)
    text = (
        f"📮 Service : {svc_display_name(service_name)}\n"
        f"🌐 Country :  {flag} {html.escape(display_country)}\n"
        f"⏳ Waiting for OTP"
    )

    rows_kb = []
    for pn in phone_nums:
        copy_num = f"+{pn}" if not str(pn).startswith("+") else str(pn)
        rows_kb.append([
            btn(copy_num, get_svc_icon(service_name), copy_text=copy_num, style="success")
        ])

    # Keep the normal controls: Change Number is the pagination action here,
    # while Change Country + Prefix remain side-by-side as before.
    rows_kb.append([
        btn("Change Number", E_TOOL_CHANGENUMBER,
            callback_data="hadi_prefix_more", style="danger")
    ])
    rows_kb.append([
        btn("Change Country", E_MENU_GLOBE2,
            callback_data=f"hadi_chgctry_{service_name}", style="primary"),
        btn("Prefix", E_MENU_GLOBE2,
            callback_data=f"hadi_prefix_{service_name}", style="primary"),
    ])
    rows_kb.append([
        btn("OTP Group", E_MENU_OTPGROUP, url=f"https://t.me/{OTP_GROUP[1:]}")
    ])

    try:
        await _tg_client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": rows_kb},
            },
            timeout=8.0,
        )
    except Exception as e:
        print(f"[Prefix] Failed to load next 3 numbers: {e}")


@dp.callback_query(F.data.startswith("hadi_chgctry_"))
async def cb_hadi_chgctry(c: types.CallbackQuery):
    try:
        await c.answer()
    except Exception:
        pass
        
    chat_id = c.message.chat.id
    service_name = c.data.split("_", 2)[2]
    
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE numbers SET status = 'retired', assigned_to = NULL, assign_time = NULL WHERE assigned_to = ? AND status = 'busy'",
            (chat_id,)
        )
        await db.commit()
        
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT DISTINCT country FROM numbers WHERE service = ? AND status = 'available'",
            (service_name,)
        ) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} No countries available for this service right now.")
        return
        
    country_rows = []
    for r in rows:
        cname = r[0]
        flag_id = get_flag_id_by_country(cname)
        cname_escaped = cname.replace(" ", "_")
        country_rows.append([
            {
                "text": f"{country_flag_emoji(cname)} {cname}",
                "callback_data": f"hadi_ctry_{service_name}_{cname_escaped}",
                "style": "primary"}
        ])
    country_rows.append([
        {"text": "Back", "callback_data": "menu_stock_home", "style": "primary"},
    ])
    
    await safe_edit(
        c.message,
        f"{ce(E_MENU_GLOBE2, '🌐')} <b>Select a Country:</b>",
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

        async with aiosqlite.connect(DB_FILE) as db:
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
                    "AND NOT EXISTS (SELECT 1 FROM number_usage_history h WHERE h.phone_number = numbers.phone_number) "
                    f"AND phone_number NOT IN ({placeholders}) "
                    "ORDER BY RANDOM() LIMIT 3"
                )
                params = [service_name, country_name, *old_phones]
            else:
                query = (
                    "SELECT phone_number, otp_rate FROM numbers "
                    "WHERE service = ? AND country = ? AND status = 'available' "
                    "AND NOT EXISTS (SELECT 1 FROM number_usage_history h WHERE h.phone_number = numbers.phone_number) "
                    "ORDER BY RANDOM() LIMIT 3"
                )
                params = [service_name, country_name]

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

            if not rows:
                await safe_edit(
                    c.message,
                    f"{ce(E_BROADCAST_FAIL, '❌')} <b>No different numbers available.</b>\n\nYour current number is still kept active.",
                    markup([
                        [btn("Change Number", E_TOOL_CHANGENUMBER, callback_data=f"hadi_chgnum_{service_name}_{country_name.replace(' ', '_')}", style="danger")],
                        [btn("Change Country", E_MENU_GLOBE2, callback_data=f"hadi_chgctry_{service_name}", style="primary")],
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
                    "UPDATE numbers SET status = 'busy', assigned_to = ?, assign_time = ?, otp_received = 0, sms_seen_count = 0 WHERE phone_number = ? AND status = 'available'",
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
            service_name, country_name, phone_nums, display_country, label, otp_rate=selected_rate
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
    await safe_edit(c.message, await admin_stats_text(), admin_keyboard())

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
        admin_cat_users_keyboard()
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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="primary")])
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

# ── Maintenance submenu ──
@dp.callback_query(F.data == "adm_maintenance")
async def adm_maintenance_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    maint_status = f"{ce(E_BROADCAST_FAIL, '🔴')} ON" if state.maintenance_mode else f"{ce(E_FWD_OK, '🟢')} OFF"
    text = (
        f"{ce(E_ADMIN_WRENCH, '🔧')} <b>Maintenance Control</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current Status : <b>{maint_status}</b>\n\n"
        f"When ON — all users (except admin) will see a maintenance message."
    )
    await safe_edit(c.message, text, admin_maintenance_keyboard())

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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_co_admins", style="primary")])
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
@dp.callback_query(F.data == "adm_users")
async def adm_users_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    text = (
        f"{ce(E_ADMIN_USERS, '👥')} <b>User Management</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  • Total Registered : <b>{len(users_db)}</b>\n"
        f"  • Verified         : <b>{len(state.verified_users)}</b>\n"
        f"  • Banned           : <b>{len(state.banned_users)}</b>"
    )
    await safe_edit(c.message, text, admin_users_keyboard())

@dp.callback_query(F.data == "adm_user_stats")
async def adm_user_stats_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass
    lines = []
    for uid_str, info in list(users_db.items())[-10:]:
        name = info.get("first_name", "?") + " " + info.get("last_name", "")
        uname = info.get("username", "")
        lines.append(f"  • <code>{uid_str}</code> — {name.strip()} {uname}")
    recent_text = "\n".join(lines) if lines else "  No users yet."
    text = (
        f"{ce(E_ADMIN_USERS, '👥')} <b>Recent 10 Users</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{recent_text}"
    )
    kb = markup(
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_users", style="primary")],
    )
    await safe_edit(c.message, text, kb)

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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="primary")])
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
    chunk = all_users[page*per_page:(page+1)*per_page]
    lines = []
    for uid_str, info in chunk:
        name = (info.get("first_name","") + " " + info.get("last_name","")).strip()
        uname = info.get("username","")
        banned = f" {ce(E_BROADCAST_FAIL, '🚫')}" if int(uid_str) in state.banned_users else ""
        lines.append(f"<code>{uid_str}</code> — {name} {uname}{banned}")
    text = (
        f"{ce(E_ADMIN_USERS, '👥')} <b>User List</b> (page {page+1})"
        f" — Total: <b>{total}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lines)
    )
    nav_row = []
    if page > 0:
        nav_row.append(btn("Prev", E_TOOL_BACKBUTTON, callback_data=f"adm_users_list_{page-1}", style="primary"))
    if (page+1)*per_page < total:
        nav_row.append(btn("Next", E_TOOL_REFRESHING, callback_data=f"adm_users_list_{page+1}", style="primary"))
    rows = []
    if nav_row: rows.append(nav_row)
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_users", style="primary")])
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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="primary")])
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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="primary")])
    )

# ── Bot Settings submenu ──
@dp.callback_query(F.data == "adm_country_rates")
async def adm_country_rates_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    await c.answer()
    rows = [[btn("🌍 Global Country Rates", E_MENU_GLOBE2, callback_data="adm_cr_global", style="success")]]
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
        rows.append([btn(f"🌐 {svc_display_name(svc)} — Country Rates", E_MENU_GLOBE2, callback_data=f"adm_cr_svc|{svc}", style="primary")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])
    await safe_edit(c.message, f"🌐 <b>Country Rates</b>\n\nChoose <b>Global</b> to use one country rate for all services, or choose a service to set a service-specific country rate.\n\n<b>Priority:</b> Service + Country → Global Country → Service Rate", {"inline_keyboard": rows})

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
        await safe_edit(c.message, "❌ No countries found in stock yet.", markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_country_rates", style="primary")]))
        return
    rows = []
    for country in countries:
        rate = get_global_country_rate(country)
        shown = rate if rate is not None else 0.0
        tag = "override" if rate is not None else "not set"
        rows.append([btn(f"{country} — {shown:.2f} TK ({tag})", E_MENU_GLOBE2, callback_data=f"adm_cr_global_set|{country}", style="success" if rate is not None and shown > 0 else "primary")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_country_rates", style="primary")])
    await safe_edit(c.message, "🌍 <b>Global Country Rates</b>\n\nThis rate applies to every service unless a service-specific country rate is set.", {"inline_keyboard": rows})

@dp.callback_query(F.data.startswith("adm_cr_global_set|"))
async def adm_global_country_rate_set_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    await c.answer()
    country = c.data.split("|", 1)[1]
    current = get_global_country_rate(country)
    current_text = f"{current:.2f} TK" if current is not None else "Not set"
    state.admin_pending_actions[c.from_user.id] = f"set_global_country_rate|{country}"
    await safe_edit(c.message, f"🌍 <b>Global Country Rate — {country}</b>\n\nCurrent: <b>{current_text}</b>\n\nSend the rate in TK (e.g. <code>0.40</code>). Send <code>0</code> to disable the global country rate.", markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_cr_global", style="primary")]))

@dp.callback_query(F.data.startswith("adm_cr_svc|"))
async def adm_country_rate_service_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    await c.answer()
    svc = c.data.split("|", 1)[1]
    countries = await _available_countries_for_service(svc)
    if not countries:
        await safe_edit(c.message, f"❌ No countries found for <b>{svc_display_name(svc)}</b>.", markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_country_rates", style="primary")]))
        return
    rows = []
    for country in countries:
        rate = get_country_rate(svc, country)
        global_rate = get_global_country_rate(country)
        fallback = global_rate if global_rate is not None else get_service_rate(svc)
        shown = rate if rate is not None else fallback
        tag = "service override" if rate is not None else ("global" if global_rate is not None else "service default")
        rows.append([btn(f"{country} — {shown:.2f} TK ({tag})", E_MENU_GLOBE2, callback_data=f"adm_cr_set|{svc}|{country}", style="success" if shown > 0 else "danger")])
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_country_rates", style="primary")])
    await safe_edit(c.message, f"🌐 <b>{svc_display_name(svc)} Country Rates</b>\n\nSet a service-specific rate. If you do not set one, the Global Country Rate (if any) is used.", {"inline_keyboard": rows})

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
        current_text = f"{current:.2f} TK (service override)"
    elif global_rate is not None:
        current_text = f"{global_rate:.2f} TK (global)"
    else:
        current_text = f"{get_service_rate(svc):.2f} TK (service default)"
    state.admin_pending_actions[c.from_user.id] = f"set_country_rate|{svc}|{country}"
    await safe_edit(c.message, f"🌐 <b>{svc_display_name(svc)} — {country}</b>\n\nCurrent: <b>{current_text}</b>\n\nSend the new service-specific rate in TK (e.g. <code>0.40</code>). Send <code>0</code> to disable this service-specific earning and fall back to the global country rate.", markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data=f"adm_cr_svc|{svc}", style="primary")]))

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
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await safe_edit(c.message, text, await admin_rates_keyboard())

@dp.callback_query(F.data == "adm_set_min_withdraw")
async def adm_set_min_withdraw_prompt_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    current = get_min_withdraw()
    state.admin_pending_actions[c.from_user.id] = "set_min_withdraw"

    await safe_edit(
        c.message,
        f"{ce(E_OTP_KEY, '✏️')} <b>Minimum Withdrawal Amount</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current minimum: <b>{current:.2f} TK</b>\n\n"
        f"{ce(E_MENU_PIN, '📌')} Send the new minimum withdrawal amount in TK (e.g. <code>50</code>).",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
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
        f"Current rate: <b>{current:.2f} TK</b> / successful OTP\n\n"
        f"{ce(E_MENU_PIN, '📌')} Send the new rate in TK (e.g. <code>0.40</code>). Send <code>0</code> to disable earning for this service.",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
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
        f"After adding it, the bot will ask for the <b>TK / OTP</b> rate.",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
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
    rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
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
        f"Current rate: <b>{current:.2f} TK</b> / successful OTP\n\n"
        f"{ce(E_MENU_PIN, '📌')} Send the new rate in TK (e.g. <code>0.40</code>). Send <code>0</code> to disable earning for this service.",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
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

    text = (
        "⚙️   <b>Bot Settings</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"💬 OTP History   : {hist_count}\n\n"
        f"☎️ All Number     : {stock_count}\n\n"
        f"🟢 Active Panels : {active_panels}"
    )
    await safe_edit(c.message, text, admin_settings_keyboard())

# ── Panel Management: list all panels (2 per row) ──
@dp.callback_query(F.data == "adm_panel_mgmt")
async def cb_adm_panel_mgmt(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    lines = [f"{ce(E_ADMIN_TOOL, '📡')} <b>Panel Management</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    for provider in SMS_PROVIDERS:
        label = PROVIDER_LABELS.get(provider, provider.capitalize())
        dot = '🟢' if provider_configured(provider) else '🔴'
        lines.append(f"  {dot} {label}")
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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="primary")])
    )

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
@dp.callback_query(F.data == "adm_forcejoin_mgmt")
async def cb_adm_forcejoin_mgmt(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    enabled = force_join_enabled()
    lines = [
        f"{ce(E_JOIN_CHANNEL, '🔗')} <b>Force Join</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Status : {'🟢 ON' if enabled else '🔴 OFF'}",
        "",
        f"CHANNEL: {CHANNEL_ID}",
        f"OTP GROUP: {OTP_GROUP}",
    ]

    await safe_edit(c.message, "\n".join(lines), force_join_mgmt_keyboard())

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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="primary")])
    )

# ── Force Join: remove a channel (base 2 can't be removed) ──
@dp.callback_query(F.data.regexp(r"^adm_fj_rm_([a-z0-9]+)$"))
async def cb_adm_fj_rm(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    m = re.match(r"^adm_fj_rm_([a-z0-9]+)$", c.data)
    key = m.group(1)
    if key not in state.force_join_channels or key in BASE_FORCE_JOIN_KEYS:
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
        f"{ce(E_MENU_GLOBE2, '🌐')} <b>{label} — Edit URL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} নতুন join URL পাঠান (যেমন: <code>https://t.me/yourchannel</code>):",
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data=f"adm_fj_detail_{key}", style="primary")])
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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="primary")])
    )

# ── SMS Hadi Upload Stock ──
@dp.callback_query(F.data == "adm_upload_stock")
async def cb_adm_upload_stock(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass
    
    state.admin_pending_actions[c.from_user.id] = 'upload_stock'
    await safe_edit(
        c.message,
        f"📁 <b>Upload Stock</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Send your: <code>txt</code> or <code>.xlsx</code> stock file\n\n"
        f"Auto-broadcast to your all member",
        markup([btn("Cancel ❌", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])
    )

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
        markup([btn("✖", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])
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
            markup([btn("Create Pool", E_FWD_OK, callback_data="adm_forward_pool_upload", style="success")],
                   [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_cat_stock", style="primary")])
        )
        return

    text_lines = [f"{ce(E_ADMIN_BOLT, '📦')} <b>Number Forward Pools</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    kb_rows = []
    for pool_id, service, interval, status, count in rows:
        status_label = "🟢 Active" if status == "active" else "⚪ Stopped"
        text_lines.append(f"#{pool_id} · <b>{html.escape(svc_display_name(service))}</b> · {count} numbers · {interval}s · {status_label}")
        if status == "active":
            kb_rows.append([btn(f"Stop #{pool_id} · {svc_display_name(service)}", E_BROADCAST_FAIL, callback_data=f"fpool_stop_{pool_id}", style="danger")])
    kb_rows.append([btn("Create New Pool", E_FWD_OK, callback_data="adm_forward_pool_upload", style="success")])
    kb_rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_cat_stock", style="primary")])
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
            markup([btn("Start Now", E_FWD_OK, callback_data="fpool_start", style="success")],
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
        f"{ce(E_MENU_PIN, '🌐')} <b>Select Languages for Forward Card</b>\n\n"
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
    target = FORWARD_GROUP_ID or OTP_GROUP
    target_note = (
        f"Forward target: <code>{html.escape(str(target))}</code>"
        if target else
        "⚠️ Forward target is not configured yet. Set FORWARD_GROUP_ID or OTP_GROUP before OTPs arrive."
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
               [btn("View Pools", E_ADMIN_BOLT, callback_data="adm_forward_pool_list", style="primary")])
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
        markup([btn("✖", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])
    )

# ── Direct Stock List / Delete Stock ──
DELETE_STOCK_PAGE_SIZE = 25

async def _render_delete_stock_page(message: types.Message, page: int = 0):
    """Show uploaded stock directly as country/service/count rows.
    Each row has its own Delete button. Data is read fresh from SQLite every time.
    """
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            """
            SELECT country, service, COUNT(*) AS total,
                   SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available
            FROM numbers
            GROUP BY country, service
            ORDER BY country COLLATE NOCASE, service COLLATE NOCASE
            """
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        state.delstock_pairs = []
        state.delstock_page = 0
        await safe_edit(
            message,
            f"{ce(E_BROADCAST_FAIL, '❌')} <b>Delete Stock</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"No uploaded stock is currently registered in the database.",
            admin_keyboard()
        )
        return

    page_count = max(1, (len(rows) + DELETE_STOCK_PAGE_SIZE - 1) // DELETE_STOCK_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    state.delstock_page = page

    page_rows = rows[page * DELETE_STOCK_PAGE_SIZE:(page + 1) * DELETE_STOCK_PAGE_SIZE]
    state.delstock_pairs = [(str(country), str(service)) for country, service, _total, _available in page_rows]

    total_stock = sum(int(r[2] or 0) for r in rows)
    available_stock = sum(int(r[3] or 0) for r in rows)

    keyboard_rows = []
    for idx, (country, service, total, available) in enumerate(page_rows):
        label = f"🗑️ {country} • {svc_display_name(service)} • {int(total)}"
        keyboard_rows.append([
            btn(label, E_BROADCAST_FAIL, callback_data=f"adm_delsvc_{idx}", style="danger")
        ])

    nav = []
    if page > 0:
        nav.append(btn("⬅️ Previous", E_TOOL_BACKBUTTON, callback_data=f"adm_delstock_pg_{page - 1}", style="primary"))
    if page < page_count - 1:
        nav.append(btn("Next ➡️", E_TOOL_BACKBUTTON, callback_data=f"adm_delstock_pg_{page + 1}", style="primary"))
    if nav:
        keyboard_rows.append(nav)
    keyboard_rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])

    await safe_edit(
        message,
        f"{ce(E_ADMIN_WRENCH, '🗑️')} <b>Delete Stock</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>Total stock:</b> {total_stock}\n"
        f"✅ <b>Available:</b> {available_stock}\n"
        f"📄 <b>Page:</b> {page + 1}/{page_count}\n\n"
        f"📌 <i>Uploaded stock is shown directly below. Tap a stock row to delete that country + service stock.</i>",
        {"inline_keyboard": keyboard_rows}
    )

@dp.callback_query(F.data == "adm_delete_stock")
async def cb_adm_delete_stock(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        return
    try:
        await c.answer()
    except Exception:
        pass
    await _render_delete_stock_page(c.message, 0)

@dp.callback_query(F.data.startswith("adm_delstock_pg_"))
async def cb_adm_delstock_page(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        return
    try:
        await c.answer()
    except Exception:
        pass
    try:
        page = int(c.data.rsplit("_", 1)[1])
    except Exception:
        page = 0
    await _render_delete_stock_page(c.message, page)

@dp.callback_query(F.data.startswith("adm_delsvc_"))
async def cb_adm_delsvc(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        return
    try:
        await c.answer()
    except Exception:
        pass

    try:
        idx = int(c.data.split("_", 2)[2])
    except Exception:
        await c.answer("Invalid stock selection.", show_alert=True)
        return

    if idx < 0 or idx >= len(state.delstock_pairs):
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '❌')} This stock list has expired. Please open Delete Stock again.",
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
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '🗑️')} <b>Deleted successfully</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌍 <b>Country:</b> {country}\n"
            f"📱 <b>Service:</b> {svc_display_name(service)}\n"
            f"📦 <b>Numbers deleted:</b> {deleted}\n\n"
            f"<i>Other country/service stock was not changed.</i>",
            markup([btn("🗑️ Delete Stock", E_ADMIN_WRENCH, callback_data="adm_delete_stock", style="danger")],
                   [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])
        )
    else:
        await safe_edit(
            c.message,
            f"{ce(E_BROADCAST_FAIL, '⚠️')} That stock is already empty.",
            admin_keyboard()
        )

# ── Download Unused Stock ──
@dp.callback_query(F.data == "adm_download_unused")
async def cb_adm_download_unused(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT service, COUNT(*) FROM numbers WHERE status = 'available' GROUP BY service ORDER BY service"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} No unused stock numbers available right now.", admin_keyboard())
        return

    state.dlunused_svc_list = [r[0] for r in rows]
    total = sum(r[1] for r in rows)

    kb_rows = []
    for idx, (svc, count) in enumerate(rows):
        kb_rows.append([btn(f"{svc_display_name(svc)}  —  {count}", E_ADMIN_BOLT, callback_data=f"adm_dlunused_{idx}", style="primary")])
    kb_rows.append([btn(f"All Services  —  {total}", E_ADMIN_CASH, callback_data="adm_dlunused_all", style="success")])
    kb_rows.append([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])

    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_BOLT, '📦')} <b>Download Unused Stock</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{ce(E_MENU_PIN, '📌')} কোন সার্ভিসের unused numbers ডাউনলোড করতে চান, সিলেক্ট করুন:",
        {"inline_keyboard": kb_rows}
    )

async def _send_unused_stock_file(c: types.CallbackQuery, service: str | None):
    query = "SELECT phone_number, service, country FROM numbers WHERE status = 'available'"
    params = ()
    if service is not None:
        query += " AND service = ?"
        params = (service,)
    query += " ORDER BY country, service, phone_number"

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await c.message.answer(f"{ce(E_BROADCAST_FAIL, '❌')} No unused stock numbers found for this selection.", parse_mode="HTML")
        return

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["phone_number", "service", "country"])
    writer.writerows(rows)
    file_bytes = buf.getvalue().encode("utf-8")

    tag = svc_display_name(service).replace(" ", "_") if service else "All_Services"
    filename = f"unused_stock_{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    doc = types.BufferedInputFile(file_bytes, filename=filename)

    await c.message.answer_document(
        doc,
        caption=(
            f"{ce(E_ADMIN_BOLT, '📦')} <b>Unused Stock Numbers</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '🏷')} <b>Service:</b>  {svc_display_name(service) if service else 'All Services'}\n"
            f"{ce(E_ADMIN_OTP, '📲')} <b>Total Unused:</b>  <b>{len(rows)}</b>\n"
            f"{ce(E_ADMIN_DATE, '📅')} <b>Exported:</b>  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "adm_dlunused_all")
async def cb_adm_dlunused_all(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer("Preparing file...")
    except Exception: pass
    await _send_unused_stock_file(c, None)

@dp.callback_query(F.data.startswith("adm_dlunused_"))
async def cb_adm_dlunused_svc(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer("Preparing file...")
    except Exception: pass

    idx_str = c.data[len("adm_dlunused_"):]
    try:
        idx = int(idx_str)
    except ValueError:
        return
    if idx < 0 or idx >= len(state.dlunused_svc_list):
        await c.message.answer(f"{ce(E_BROADCAST_FAIL, '❌')} Selection expired — please open Download Unused Stock again.", parse_mode="HTML")
        return

    service = state.dlunused_svc_list[idx]
    await _send_unused_stock_file(c, service)

# ── Multi-Provider Settings Inputs (base panels + any dynamically added ones) ──
# NOTE: regex টা generic রাখা হয়েছে ("|".join(SMS_PROVIDERS) দিয়ে নয়), কারণ
# @dp.callback_query decorator শুধু একবার, bot import হওয়ার সময় রেজিস্টার হয় —
# তাই runtime এ নতুন panel add হলে সেই compile-time regex আর আপডেট হতো না।
# তার বদলে handler এর ভেতরে runtime এ SMS_PROVIDERS list check করা হচ্ছে।
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
        markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data=f"adm_panel_detail_{provider}", style="primary")])
    )

# ── Shared helper: insert/update stock numbers with given service ──
async def _save_stock_numbers(numbers, service_name, country_name, upload_rate=None) -> int:
    """Persist uploaded stock defensively. Bad/duplicate rows are skipped
    instead of crashing the whole upload; valid rows are always kept.
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

                    # Upload rate is the only rate used for this stock batch.
                    # Any rate embedded in the uploaded file is intentionally ignored.
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

                    # The phone_number column is the primary key. REPLACE is
                    # avoided because it could wipe assignment/status fields.
                    cur = await db.execute(
                        "SELECT phone_number FROM numbers WHERE phone_number = ?", (num,)
                    )
                    exists = await cur.fetchone()
                    if exists:
                        # Never reset an actively leased number during a re-upload.
                        # The old code could turn a busy number back to available and
                        # erase assigned_to/assign_time, causing a live user's number
                        # to be handed to somebody else. Preserve busy/retired state.
                        await db.execute(
                            """UPDATE numbers
                               SET service = ?, country = ?, otp_rate = ?,
                                   status = CASE WHEN status IN ('busy', 'retired') THEN status WHEN EXISTS (SELECT 1 FROM number_usage_history h WHERE h.phone_number = numbers.phone_number) THEN 'retired' ELSE 'available' END,
                                   assigned_to = CASE WHEN status = 'busy' THEN assigned_to ELSE NULL END,
                                   assign_time = CASE WHEN status = 'busy' THEN assign_time ELSE NULL END,
                                   otp_received = CASE WHEN status = 'busy' THEN otp_received ELSE 0 END,
                                   sms_seen_count = CASE WHEN status = 'busy' THEN sms_seen_count ELSE 0 END
                             WHERE phone_number = ?""",
                            (service_name, item_country, item_rate, num)
                        )
                    else:
                        await db.execute(
                            "INSERT INTO numbers (phone_number, service, country, otp_rate, status, assigned_to, assign_time, otp_received, sms_seen_count) VALUES (?, ?, ?, ?, 'available', NULL, NULL, 0, 0)",
                            (num, service_name, item_country, item_rate)
                        )
                    added_count += 1
                except Exception as row_err:
                    # One malformed row must never abort the entire upload.
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
    numbers = temp_data["numbers"]
    country_name = temp_data["country"]
    service_name = temp_data["service"]
    is_custom = temp_data.get("custom", False)

    # The upload-specific rate is the single final rate for this stock batch.
    upload_rate = temp_data.get("upload_rate")
    if upload_rate is None:
        raise ValueError("Stock upload rate was not set")
    added_count = await _save_stock_numbers(numbers, service_name, country_name, upload_rate=upload_rate)
    rate = float(upload_rate)

    await m.answer(
        f"{ce(E_FWD_OK, '✅')} Stock upload completed!\n\n"
        f"{ce(E_ADMIN_TOOL, '🛠')} Service: {svc_tag_icon(service_name)} <b>{svc_display_name(service_name)}</b>{' <i>(custom)</i>' if is_custom else ''}\n"
        f"{ce(E_OTP_GLOBE, '🌍')} Country: <b>{country_name}</b>\n"
        f"{ce(E_ADMIN_CASH, '💰')} OTP Rate: <b>{rate:.2f} TK</b>",
        reply_markup=admin_keyboard()
    )

    await _broadcast_new_stock(service_name, country_name, added_count, rate)
# ── Shared helper: broadcast "new stock added" message to known users ──
async def _broadcast_new_stock(service_name, country_name, added_count, rate: float = None):
    # Channel broadcast is independent of known users. The previous version
    # returned early when no users were known, so the channel card was never sent.
    if rate is None:
        rate = get_stock_rate(service_name)

    flag = country_flag_emoji(country_name)
    price_line = (
        f"{ce(E_ADMIN_CASH, '💰')} <b>OTP Price: {rate:.4f} TK</b>"
        if rate is not None and rate > 0 else ""
    )
    channel_text = (
        f"🆕 <b>New Stock Added</b> ✨\n\n"
        f"{flag} <b>{html.escape(country_name.upper())}</b> | "
        f"{svc_tag_icon(service_name)} <b>{html.escape(svc_display_name(service_name).upper())}</b>\n\n"
        f"{price_line}"
    )
    country_escaped = country_name.replace(" ", "_")
    # Channel must never expose stock numbers. The button only opens the bot.
    channel_kb = markup([
        btn("GET NUMBER", E_RK_GET_NUM,
            url=FWD_GET_NUMBER_URL,
            style="success")
    ])

    # Always try the configured channel, even if there are no known users.
    if CHANNEL_ID:
        try:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=channel_text,
                parse_mode="HTML",
                reply_markup=channel_kb,
            )
            print(f"[StockChannel] Card sent: {service_name} / {country_name} / {added_count}")
        except Exception as err:
            print(f"[StockChannel] Send error: {err}")

    targets = list(state.known_users)
    if not targets:
        return
    # Stock alert only: match the requested compact visual layout.
    # The alert uses the uploaded stock's country/service/rate; it does not add
    # any extra service or country information.
    flag = country_flag_emoji(country_name)
    price_line = f"{ce(E_ADMIN_CASH, '💰')} <b>OTP Price: {rate:.4f} TK</b>" if rate is not None and rate > 0 else ""
    broadcast_msg = (
        f"🆕 <b>New Stock Added</b> ✨\n\n"
        f"{flag} <b>{html.escape(country_name.upper())}</b> | "
        f"{svc_tag_icon(service_name)} <b>{html.escape(svc_display_name(service_name).upper())}</b>\n\n"
        f"{price_line}"
    )
    country_escaped = country_name.replace(" ", "_")
    kb = markup([btn("GET NUMBER", E_RK_GET_NUM, callback_data=f"hadi_ctry_{service_name}_{country_escaped}", style="success")])

    # Send concurrently instead of one-by-one — a sequential loop with a sleep
    # after every message took several minutes for a large user base. A bounded
    # semaphore keeps us well under Telegram's ~30 msg/sec global limit while
    # sending dozens of messages in parallel instead of one at a time.
    sem = asyncio.Semaphore(20)

    async def _send_one(target_uid):
        async with sem:
            try:
                await bot.send_message(target_uid, broadcast_msg, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass

    await asyncio.gather(*[_send_one(t) for t in targets])

# ── Stock Numbers allocation service callbacks ──
@dp.callback_query(F.data.startswith("adm_uploadsvc_"))
async def cb_adm_uploadsvc(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id): return
    try: await c.answer()
    except Exception: pass
    
    uid = c.from_user.id
    service_name = c.data.split("_", 2)[2]
    
    temp_data = state.admin_temp_data.pop(uid, None)
    if not temp_data:
        await safe_edit(c.message, f"{ce(E_BROADCAST_FAIL, '❌')} Session expired! Please re-upload.")
        return
        
    numbers = temp_data["numbers"]
    country_name = temp_data["country"]

    state.admin_temp_data[uid] = {
        "numbers": numbers, "country": country_name, "service": service_name, "custom": False
    }

    # Rate is set once during this upload. It is stored on every uploaded
    # number (otp_rate), so there is no second stock-rate setup later.
    state.admin_pending_actions[uid] = "upload_stock_rate"
    await safe_edit(
        c.message,
        f"{ce(E_ADMIN_CASH, '💰')} <b>Set OTP Rate</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"এই upload-এর সব number-এর প্রতি successful OTP-এর rate (TK) পাঠান।\n"
        f"উদাহরণ: <code>0.50</code>\n\n"
        f"📌 এই rate upload-এর সাথে save হবে — পরে আবার rate set করতে হবে না।",
        markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_upload_stock", style="primary")])
    )

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
        markup([btn("↩️ Back to List", E_TOOL_BACKBUTTON, callback_data="svcemoji_pg_0", style="primary")],
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

# ================= MAINTENANCE TOGGLE =================
@dp.callback_query(F.data == "toggle_maintenance")
async def toggle_maintenance(c: types.CallbackQuery):
    try:
        await c.answer()
    except Exception:
        pass
    if not is_admin(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True)
        return
    state.maintenance_mode = not state.maintenance_mode
    status = "ON" if state.maintenance_mode else "OFF"
    await c.answer(f"Maintenance Mode: {status}", show_alert=True)
    await safe_edit(c.message, await admin_stats_text(), admin_keyboard())

# ================= REFER COMMISSION NOTIFY TOGGLE =================
@dp.callback_query(F.data == "toggle_refer_notify")
async def toggle_refer_notify_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        try: await c.answer("❌ Access Denied!", show_alert=True)
        except Exception: pass
        return
    try: await c.answer()
    except Exception: pass

    await set_refer_notify_enabled(not is_refer_notify_enabled())
    status = "ON" if is_refer_notify_enabled() else "OFF"
    await c.answer(f"Refer Commission Notify: {status}", show_alert=True)

    await safe_edit(c.message, "ㅤ", await admin_rates_keyboard())

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
    if is_maintenance(uid):
        await m.answer(f"{ce(E_ADMIN_WRENCH, '🔧')} Under maintenance.")
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
            reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu", style="primary")])
        )
        return

    sent = await m.answer(
        f"{ce(E_SVC_LIST, '📋')} <b>Choose a service</b>",
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
    if is_maintenance(uid):
        await m.answer(f"🔧 <b>Under maintenance.</b>", parse_mode="HTML")
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
        [btn("🔄 Refresh", E_TOOL_REFRESHING, callback_data="live_traffic_refresh", style="primary")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu", style="primary")],
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
        [btn("🔄 Refresh", E_TOOL_REFRESHING, callback_data="live_traffic_refresh", style="primary")],
        [btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu", style="primary")],
    ))

@dp.message(F.text.in_({"2F Auth", "🔐 2F Auth"}), F.chat.type == "private")
async def rk_2fa(m: types.Message):
    uid = m.from_user.id
    if uid not in state.verified_users:
        return
    if is_maintenance(uid):
        await m.answer("🔧 <b>Under maintenance.</b>", parse_mode="HTML")
        return
    await m.answer(
        "🔐 <b>2F Auth</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Two-factor authentication settings are not configured in this bot yet.",
        parse_mode="HTML",
        reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="menu", style="primary")])
    )

@dp.message(F.text.in_({"Admin Panel", "⚙️ Admin Panel"}), F.chat.type == "private")
async def rk_admin_panel(m: types.Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    await m.answer(await admin_stats_text(), reply_markup=admin_keyboard())

# ================= SUPPORT =================

@dp.message(F.text.in_({"Support", "💬 Support"}), F.chat.type == "private")
async def rk_support(m: types.Message):
    uid = m.from_user.id
    if uid not in state.verified_users:
        return
    if is_maintenance(uid):
        await m.answer(f"{ce(E_ADMIN_WRENCH, '🔧')} Under maintenance.")
        return
    # Remove the previous button message, but keep this newly pressed button message.
    await _track_reply_button_message(m)
    # Switching to Support removes any active Get Number/Profile screen.
    await _clear_previous_service_flow(uid)
    await _release_other_stock_services(m.chat.id)
    for phone, info in list(state.active_orders.items()):
        if info.get("chat_id") == m.chat.id:
            state.active_orders.pop(phone, None)

    username = SUPPORT_USERNAME.lstrip("@")
    # Minimal support UI: only the requested text and one compact green button.
    sent = await m.answer(
        "💬 <b>Contact Support</b> 👇",
        reply_markup=markup([
            btn("💬 Contact Support", E_BROADCAST, url=f"https://t.me/{username}", style="success")
        ]),
        parse_mode="HTML",
    )
    _remember_service_flow(uid, sent, "support")

# ================= BALANCE & WITHDRAWAL HANDLERS =================

@dp.callback_query(F.data == "user_withdraw")
async def cb_user_withdraw(c: types.CallbackQuery):
    uid = c.from_user.id
    bal = await get_balance(uid)
    min_wd = get_min_withdraw()
    if bal < min_wd:
        await c.answer(f"⚠️ Minimum withdrawal is {min_wd:.2f} TK!", show_alert=True)
        return

    await c.answer()
    state.pending_withdrawal_amount[uid] = True
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
        "💸 <b>ENTER AMOUNT TO WITHDRAW:</b>"
    )
    kb = markup(
        [btn("CANCEL", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")]
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
        [btn("Withdraw Balance", E_ADMIN_BOLT, callback_data="user_withdraw", style="success")]
    )
    await safe_edit(c.message, text, kb)

@dp.callback_query(F.data.startswith("withdraw_opt_"))
async def cb_withdraw_opt(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    amount = state.pending_withdrawal_method.get(uid)
    if amount is None:
        await c.answer("⚠️ Session expired! Please start withdrawal again.", show_alert=True)
        return

    bal = await get_balance(uid)
    if bal < amount:
        await c.answer("⚠️ Insufficient balance!", show_alert=True)
        return

    method = c.data.split("_")[-1] # "binance", "nagad" or "bkash"
    method_titles = {
        "binance": "Binance UID",
        "nagad": "Nagad Number",
        "bkash": "Bkash Number"}
    method_title = method_titles.get(method, method.title())

    state.pending_withdrawal_method.pop(uid, None)
    state.pending_withdrawal_details[uid] = {
        "method": method_title,
        "amount": amount
    }
    
    prompt_text = (
        f"📤 Send your <b>{method_title}</b> — <code>{amount:.2f} TK</code>"
    )
    await safe_edit(
        c.message,
        prompt_text,
        markup([btn("CANCEL", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
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
        async with db.execute("SELECT user_id, amount, method, details, status FROM withdrawals WHERE id = ?", (w_id,)) as cursor:
            row = await cursor.fetchone()
            
    if not row:
        await c.answer("❌ Withdrawal request not found!", show_alert=True)
        return
        
    user_id, amount, method, details, status = row
    
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
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 <b>Withdrawal Approved!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"💰 <b>Amount:</b> <code>{amount:.2f} TK</code>\n"
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
                    f"💰 <b>Amount:</b> <code>{amount:.2f} TK</code> has been refunded to your bot balance.\n"
                    f"🏦 <b>Method:</b> <code>{method}</code>\n\n"
                    f"⚠️ Please verify your details and try again."
                )
            )
        except Exception:
            pass

# ── Admin text input handler (ban/unban/viewstats tokens) ──
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
            or uid in state.prefix_filter_pending):
        raise SkipHandler()

    pending_action = state.admin_pending_actions.get(uid)
    if pending_action in {"forward_pool_service", "forward_pool_interval"}:
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
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="primary")])
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

# ================= DOCUMENT FILE UPLOAD =================

@dp.message(F.chat.type == "private", F.document, F.from_user.id.in_(ADMIN_IDS))
async def handle_document_upload(m: types.Message):
    uid = m.from_user.id
    action = state.admin_pending_actions.get(uid)

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
                f"{ce(E_OTP_GLOBE, '🌍')} Countries: <b>{country_summary}</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} এখন এই pool-এর <b>service name</b> পাঠান "
                f"(যেমন <code>Facebook</code>, <code>WhatsApp</code>, <code>Custom SMS</code>)।",
                reply_markup=markup([btn("✖", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])
            )
        except Exception as err:
            await status_msg.edit_text(
                f"{ce(E_BROADCAST_FAIL, '❌')} Failed to parse Number Forward Pool: <code>{html.escape(str(err))}</code>",
                reply_markup=admin_keyboard()
            )
        return

    if action != 'upload_stock':
        return
        
    state.admin_pending_actions.pop(uid, None)
    status_msg = await m.answer(f"{ce(E_NUM_WAITING, '⏳')} Downloading and parsing stock file...")
    
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

        state.admin_temp_data[uid] = {"numbers": stock_items, "country": country_name}

        markup_kb = []
        for s in ALLOWED_SERVICES:
            markup_kb.append([btn(s, E_NUM_SERVICE, callback_data=f"adm_uploadsvc_{s}", style="primary")])
        markup_kb.append([btn("Custom Service (Type Name)", E_ADMIN_TOOL, callback_data="adm_stock_custom_svc", style="success")])
        markup_kb.append([btn("Cancel", E_BROADCAST_FAIL, callback_data="admin_panel", style="danger")])
        
        await status_msg.edit_text(
            f"{ce(E_RK_GET_NUM, '📱')} Found <b>{len(stock_items)}</b> valid phone numbers in stock file.\n"
            f"{ce(E_OTP_GLOBE, '🌍')} Auto-detected Country: <b>{country_name}</b>\n"
            f"⚠️ Skipped/duplicate rows: <b>{skipped_rows}</b>\n\n"
            f"Select a service",
            reply_markup={"inline_keyboard": markup_kb},
            parse_mode="HTML"
        )
    except Exception as e:
        await status_msg.edit_text(f"{ce(E_BROADCAST_FAIL, '❌')} Failed to parse stock file: {str(e)}")

# ================= PRIVATE MESSAGE HANDLER =================

@dp.message(F.chat.type == "private")
async def handle_private_message(m: types.Message):
    uid = m.from_user.id

    # ── Prefix filter input ──
    if uid in state.prefix_filter_pending and m.text:
        pending = state.prefix_filter_pending.pop(uid)
        raw_prefix = m.text.strip()
        prefix = re.sub(r"\D", "", raw_prefix)
        # Accept +880..., 880..., or 00880... input. Stock numbers are stored
        # as normalized digits, so the filter always compares digit prefixes.
        if prefix.startswith("00"):
            prefix = prefix[2:]

        if not prefix:
            state.prefix_filter_pending[uid] = pending
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid prefix.</b>\n\n"
                f"Send the starting digits, for example <code>88019255</code>."
            )
            return

        service_name = pending["service"]
        message_id = pending["message_id"]
        chat_id = pending["chat_id"]

        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                "SELECT phone_number, otp_rate FROM numbers "
                "WHERE service = ? AND status = 'available' "
                "AND phone_number LIKE ? "
                "AND NOT EXISTS (SELECT 1 FROM number_usage_history h "
                "                WHERE h.phone_number = numbers.phone_number) "
                "ORDER BY phone_number LIMIT 3",
                (service_name, prefix + "%")
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            state.prefix_filter_pending[uid] = pending
            try:
                await _tg_client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": (
                            f"{ce(E_BROADCAST_FAIL, '❌')} <b>No numbers found.</b>\n\n"
                            f"Service: <b>{html.escape(svc_display_name(service_name))}</b>\n"
                            f"Prefix: <code>{html.escape(prefix)}</code>\n\n"
                            f"Try another prefix."
                        ),
                        "parse_mode": "HTML",
                        "reply_markup": markup([
                            [btn("↩️ Back", E_TOOL_BACKBUTTON,
                                 callback_data=f"hadi_prefix_back_{service_name}", style="primary")]
                        ]),
                    },
                    timeout=8.0,
                )
            except Exception as e:
                print(f"[Prefix] Failed to show no-results state: {e}")
            return

        # Edit the original number-card message rather than creating another service card.
        # No overall result cap: the active state lets the user fetch the next 3.
        state.prefix_filter_active[uid] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "service": service_name,
            "prefix": prefix,
            "offset": len(rows),
        }
        phone_nums = [r[0] for r in rows]
        display_country = get_country(phone_nums[0]) if phone_nums else ""
        flag = country_flag_emoji(display_country)
        text = (
            f"📮 Service : {svc_display_name(service_name)}\n"
            f"🌐 Country :  {flag} {html.escape(display_country)}\n"
            f"⏳ Waiting for OTP"
        )

        num_btns = []
        for pn in phone_nums:
            copy_num = f"+{pn}" if not str(pn).startswith("+") else str(pn)
            num_btns.append(
                btn(copy_num, E_NUM_PHONE,
                    copy_text=copy_num, style="success")
            )

        rows_kb = [[item] for item in num_btns]
        rows_kb.append([
            btn("Change Number", E_TOOL_CHANGENUMBER,
                callback_data="hadi_prefix_more", style="danger")
        ])
        rows_kb.append([
            btn("Change Country", E_MENU_GLOBE2,
                callback_data=f"hadi_chgctry_{service_name}", style="primary"),
            btn("Prefix", E_MENU_GLOBE2,
                callback_data=f"hadi_prefix_{service_name}", style="primary"),
        ])
        rows_kb.append([btn("OTP Group", E_MENU_OTPGROUP, url=f"https://t.me/{OTP_GROUP[1:]}")])

        try:
            await _tg_client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": rows_kb},
                },
                timeout=8.0,
            )
        except Exception as e:
            print(f"[Prefix] Failed to update number card: {e}")
        return

    # ── Withdrawal Amount handler ──
    if uid in state.pending_withdrawal_amount and m.text:
        raw_val = m.text.strip().replace(",", ".")
        bal = await get_balance(uid)
        min_wd = get_min_withdraw()

        try:
            amount = float(raw_val)
        except ValueError:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Invalid number, e.g. 50",
                reply_markup=markup([btn("CANCEL", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
            )
            return

        if amount < min_wd:
            await m.answer(
                f"⚠️ Minimum is <code>{min_wd:.2f} TK</code>",
                reply_markup=markup([btn("CANCEL", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
            )
            return

        if amount > bal:
            await m.answer(
                f"⚠️ Insufficient balance, you have <code>{bal:.2f} TK</code>",
                reply_markup=markup([btn("CANCEL", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")])
            )
            return

        state.pending_withdrawal_amount.pop(uid, None)
        state.pending_withdrawal_method[uid] = amount

        otp_stats = await get_user_otp_stats(uid)
        total_otp = sum(int(item.get("count", 0)) for item in otp_stats.values())
        daily_otp = await get_today_otp_count(uid)
        bal_now = await get_balance(uid)
        min_wd = get_min_withdraw()

        text = (
            "《 😔 WITHDRAWAL 》\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 <b>TOTAL OTP:</b> <code>{total_otp}</code>\n"
            f"📅 <b>TODAY OTP:</b> <code>{daily_otp}</code>\n"
            f"💰 <b>BALANCE:</b> <code>{bal_now:.2f} TK</code>\n"
            f"🔐 <b>MINIMUM WITHDRAW:</b> <code>{min_wd:.2f} TK</code>\n\n"
            f"<b>SELECT METHOD:</b>\n"
            f"💸 <b>AMOUNT:</b> <code>{amount:.2f} TK</code>"
        )
        kb = markup(
            [btn("NAGAD", callback_data="withdraw_opt_nagad", style="primary"),
             btn("BKASH", E_WD_BKASH, callback_data="withdraw_opt_bkash", style="primary")],
            [btn("BINANCE UID", E_WD_BINANCE, callback_data="withdraw_opt_binance", style="primary")],
            [btn("CANCEL", E_BROADCAST_FAIL, callback_data="back_to_balance", style="danger")]
        )
        await m.answer(text, reply_markup=kb)
        return

    # ── Withdrawal Details handler ──
    if uid in state.pending_withdrawal_details and m.text:
        w_info = state.pending_withdrawal_details.pop(uid)
        details = m.text.strip()
        amount = w_info["amount"]
        method = w_info["method"]
        
        # Balance validation to prevent race conditions
        success = await deduct_balance(uid, amount)
        if not success:
            await m.answer("⚠️ Insufficient balance to process this withdrawal request.")
            return
            
        # Register withdrawal in SQLite
        async with aiosqlite.connect(DB_FILE) as db:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await db.execute(
                "INSERT INTO withdrawals (user_id, method, details, amount, status, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, method, details, amount, 'pending', timestamp)
            )
            await db.commit()
            
            # Fetch last inserted ID to use in action buttons
            async with db.execute("SELECT last_insert_rowid()") as cursor:
                w_id = (await cursor.fetchone())[0]
                
        # User confirmation
        await m.answer(
            f"✅ <code>{amount:.2f} TK</code> via {method} → <code>{details}</code>, pending review",
        )
        
        # Notify Admin
        admin_text = (
            f"🔔 <b>New Withdrawal Request!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>User ID:</b> <code>{uid}</code>\n"
            f"👤 <b>Username:</b> {m.from_user.mention_html() or 'None'}\n"
            f"💰 <b>Amount:</b> <code>{amount:.2f} TK</code>\n"
            f"🏦 <b>Method:</b> <code>{method}</code>\n"
            f"📝 <b>Details:</b> <code>{details}</code>"
        )
        admin_kb = markup(
            [btn("Approve", E_FWD_OK, callback_data=f"adm_pay_appr_{w_id}", style="success"),
             btn("Decline", E_BROADCAST_FAIL, callback_data=f"adm_pay_decl_{w_id}", style="danger")],
        )
        for admin_uid in {ADMIN_ID} | state.co_admin_ids:
            try:
                await bot.send_message(chat_id=admin_uid, text=admin_text, reply_markup=admin_kb)
            except Exception as e:
                print(f"[Admin Notify] Failed for {admin_uid}: {e}")
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
            reply_markup=markup([btn("✖", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")])
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
            f"{ce(E_MENU_PIN, '🌐')} <b>কোন কোন language-এ forward card দেখাতে চান?</b>\n"
            f"একাধিক language select করে <b>Done</b> চাপুন।",
            reply_markup=forward_pool_language_keyboard([])
        )
        return

    # ── Stock Upload: Custom Service Name Text handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == 'upload_stock_custom_svc' and m.text:
        state.admin_pending_actions.pop(uid, None)
        service_name = m.text.strip()

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

        # Ask for the upload-specific rate before the emoji step. The rate is
        # saved with the uploaded numbers and is the final rate for this stock.
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "INSERT OR IGNORE INTO earning_services (service_name, created_at, enabled) VALUES (?, ?, 1)",
                (service_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            await db.commit()

        state.admin_pending_actions[uid] = "upload_stock_rate"
        await m.answer(
            f"{ce(E_ADMIN_CASH, '💰')} <b>Set OTP Rate</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Service: <b>{svc_display_name(service_name)}</b>\n\n"
            f"এই upload-এর সব number-এর প্রতি successful OTP-এর rate (TK) পাঠান।\n"
            f"উদাহরণ: <code>0.50</code>\n\n"
            f"📌 এই rate upload-এর সাথে save হবে — পরে আবার rate set করতে হবে না.",
            reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_upload_stock", style="primary")])
        )
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
                reply_markup=markup([btn("↩️ Back to List", E_TOOL_BACKBUTTON, callback_data="svcemoji_pg_0", style="primary")])
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
        temp_data = state.admin_temp_data.get(uid)
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
                f"শুধু 0-এর বেশি একটি rate পাঠান, যেমন <code>0.50</code>।",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_upload_stock", style="primary")])
            )
            return

        temp_data["upload_rate"] = upload_rate
        state.admin_pending_actions.pop(uid, None)

        # Custom service needs the emoji selection after the rate step.
        if temp_data.get("custom", False):
            state.admin_pending_actions[uid] = "upload_stock_emoji_after_custom"
            await m.answer(
                f"{ce(E_FWD_OK, '✅')} Upload rate saved: <b>{upload_rate:.2f} TK</b> / OTP\n\n"
                f"{ce(E_MENU_PIN, '📋')} <b>Select Service (for Emoji)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Service Name: <b>{svc_display_name(temp_data['service'])}</b>\n\n"
                f"এই stock-এর OTP Forward Card-এ কোন service-এর emoji দেখাতে চান, নিচ থেকে select করুন:",
                reply_markup=emoji_picker_keyboard(0)
            )
            return

        # Fixed service: rate step is the final upload step.
        state.admin_temp_data.pop(uid, None)
        await _finalize_stock_upload(m, temp_data)
        return

    # ── Add / Deduct Balance Amount handler ──
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
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_users", style="primary")])
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
                    btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")
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
                        btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="admin_panel", style="primary")
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
            f"🌍 Country: <b>{html.escape(str(country))}</b>\n"
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
                await m.answer("❌ Invalid rate! Send a number like <code>0.40</code>.", reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_cr_global", style="primary")]))
                return
            await set_global_country_rate(country, new_rate)
            status = f"<b>{new_rate:.2f} TK</b> / OTP" if new_rate > 0 else "<i>OFF (global rate disabled)</i>"
            await m.answer(f"✅ <b>Global — {country}</b> rate updated!\n\nNew rate: {status}", reply_markup=markup([btn("↩️ Back to Global Country Rates", E_MENU_GLOBE2, callback_data="adm_cr_global", style="primary")]))
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
                await m.answer("❌ Invalid country rate! Send a number like <code>0.40</code>.", reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data=f"adm_cr_svc|{svc}", style="primary")]))
                return
            await set_country_rate(svc, country, new_rate)
            status = f"<b>{new_rate:.2f} TK</b> / OTP" if new_rate > 0 else "<i>OFF (service-specific rate disabled; global fallback will apply)</i>"
            await m.answer(f"✅ <b>{svc_display_name(svc)} — {country}</b> country rate updated!\n\nNew rate: {status}", reply_markup=markup([btn("↩️ Back to Service Country Rates", E_MENU_GLOBE2, callback_data=f"adm_cr_svc|{svc}", style="primary")]))
            return

    # ── Add unlimited earning service: Step 1 — service name ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "add_earning_service" and m.text:
        svc = m.text.strip()
        if not svc or len(svc) > 64:
            state.admin_pending_actions.pop(uid, None)
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} Invalid service name. Use 1–64 characters.",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
            )
            return

        svc_key = _rate_key(svc)
        if svc_key in {_rate_key(s) for s in RATE_SERVICES}:
            state.admin_pending_actions[uid] = f"set_rate_{svc_key}"
            await m.answer(
                f"{ce(E_MENU_PIN, '📌')} <b>{svc_display_name(svc)}</b> is already a built-in service.\n\n"
                f"Send the new rate in TK (e.g. <code>0.40</code>).",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
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
            f"Send earning amount in TK per successful OTP.\n"
            f"Example: <code>0.40</code>\n\n"
            f"Send <code>0</code> to disable earning.",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
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
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a valid number, e.g. 0.40</i>",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
            )
            return

        await set_service_rate(svc, new_rate)

        status_line = f"<b>{new_rate:.2f} TK</b> / OTP" if new_rate > 0 else "<i>OFF (earning disabled)</i>"
        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>{svc_display_name(svc)} rate updated!</b>\n\n"
            f"New rate: {status_line}",
            reply_markup=await admin_rates_keyboard()
        )
        return

    # ── Minimum Withdraw Text handler ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "set_min_withdraw" and m.text:
        state.admin_pending_actions.pop(uid, None)
        raw_val = m.text.strip().replace(",", ".")

        try:
            new_min = float(raw_val)
            if new_min < 0:
                raise ValueError
        except ValueError:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid amount!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a valid number, e.g. 50</i>",
                reply_markup=markup([btn("↩️ Back", E_TOOL_BACKBUTTON, callback_data="adm_rates", style="primary")])
            )
            return

        await set_min_withdraw(new_min)

        await m.answer(
            f"{ce(E_FWD_OK, '✅')} <b>Minimum withdrawal updated!</b>\n\n"
            f"New minimum: <b>{new_min:.2f} TK</b>",
            reply_markup=admin_rates_keyboard()
        )
        return

    # ── Add Panel wizard: Step 1 — Name ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addpanel_name" and m.text:
        raw_name = m.text.strip()
        key = sanitize_panel_key(raw_name)

        if not key or key in _RESERVED_PANEL_KEYS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>Invalid name!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name (letters/numbers only).</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="primary")])
            )
            return
        if key in SMS_PROVIDERS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>A panel with this name already exists!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name.</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="primary")])
            )
            return

        state.admin_temp_data[uid] = {"key": key, "label": raw_name}
        state.admin_pending_actions[uid] = "addpanel_token"
        await m.answer(
            f"{ce(E_OTP_KEY, '🔑')} <b>{raw_name} — API Token</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} এই panel-টার API token পাঠান:\n\n"
            f"<i>Step 2/3 — Token</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="primary")])
        )
        return

    # ── Add Panel wizard: Step 2 — API Token ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addpanel_token" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["token"] = m.text.strip()
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addpanel_url"
        await m.answer(
            f"{ce(E_MENU_GLOBE2, '🌐')} <b>{pending.get('label','Panel')} — API URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} এই panel-টার viewstats API URL পাঠান:\n\n"
            f"<i>Step 3/3 — URL</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_panel_mgmt", style="primary")])
        )
        return

    # ── Add Panel wizard: Step 3 — API URL (finalize, spawn monitor loop) ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addpanel_url" and m.text:
        state.admin_pending_actions.pop(uid, None)
        pending = state.admin_temp_data.pop(uid, None)
        url = m.text.strip()

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
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="primary")])
            )
            return
        if key in state.force_join_channels or key in BASE_FORCE_JOIN_KEYS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>এই নামে একটা channel আগে থেকেই আছে!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>একটা ভিন্ন নাম পাঠান।</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="primary")])
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
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="primary")])
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
            f"{ce(E_MENU_GLOBE2, '🌐')} <b>{pending.get('label','Channel')} — Join URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} এই channel/group এর invite/join URL পাঠান{hint}:\n\n"
            f"<i>Step 3/3 — URL</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_forcejoin_mgmt", style="primary")])
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

        await add_force_join_channel(pending["key"], pending["chat_id"], pending["label"], url)

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
        await add_force_join_channel(key, ch["chat_id"], ch["label"], new_url)

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
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
            )
            return
        if key in CAPTCHA_PANELS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>A panel with this name already exists!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name.</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
            )
            return

        state.admin_temp_data[uid] = {"key": key, "label": raw_name}
        state.admin_pending_actions[uid] = "addcap_login_url"
        await m.answer(
            f"{ce(E_MENU_GLOBE2, '🌐')} <b>{raw_name} — Login URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Panel-টার login page link পাঠান (e.g. <code>http://panel.example.com/login</code>):\n\n"
            f"<i>Step 2/9 — Login URL</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
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
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
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
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
        )
        return

    # ── Step 4 — Password ──
    if is_admin(uid) and state.admin_pending_actions.get(uid) == "addcap_password" and m.text:
        pending = state.admin_temp_data.get(uid) or {}
        pending["password"] = m.text.strip()
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addcap_msg_link"
        await m.answer(
            f"{ce(E_MENU_GLOBE2, '🌐')} <b>{pending.get('label','Panel')} — SMS/CDR Page Link</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Login-এর পর যে page-এ SMS টেবিল দেখায়, তার link পাঠান।\n"
            f"না থাকলে <code>skip</code> লিখুন — bot নিজে auto-detect করবে।\n\n"
            f"<i>Step 5/9 — Msg Link</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
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
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
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
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
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
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
            )
            return
        state.admin_temp_data[uid] = pending
        state.admin_pending_actions[uid] = "addcap_msg_col_name"
        await m.answer(
            f"{ce(E_OTP_KEY, '💬')} <b>{pending.get('label','Panel')} — Message Column Name</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} টেবিলে message column-এর header নাম পাঠান (e.g. <code>message</code>):\n\n"
            f"<i>Step 8/9 — Msg Column Name</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
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
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
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
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_captcha_mgmt", style="primary")])
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
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="primary")])
            )
            return
        if key in CAPTCHA_PANELS:
            await m.answer(
                f"{ce(E_BROADCAST_FAIL, '❌')} <b>A panel with this name already exists!</b>\n\n"
                f"{ce(E_MENU_PIN, '📌')} <i>Please send a different panel name.</i>",
                reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="primary")])
            )
            return

        state.admin_temp_data[uid] = {"key": key, "label": raw_name}
        state.admin_pending_actions[uid] = "addgp_login_url"
        await m.answer(
            f"{ce(E_MENU_GLOBE2, '🌐')} <b>{raw_name} — Login URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{ce(E_MENU_PIN, '📌')} Green Panel-টার base URL পাঠান (e.g. <code>http://143.110.245.86/</code>):\n\n"
            f"<i>Step 2/4 — Login URL</i>",
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="primary")])
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
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="primary")])
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
            reply_markup=markup([btn("Cancel", E_TOOL_BACKBUTTON, callback_data="adm_green_mgmt", style="primary")])
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

    # ── Broadcast message handler ──
    if not is_admin(uid) or state.pending_bc.get(uid) != 'waiting':
        return
    state.pending_bc.pop(uid, None)

    targets = list(state.known_users)
    if not targets:
        await m.answer(f"{ce(E_ADMIN_WRENCH, '⚠️')} No users found yet.", parse_mode="HTML")
        return

    status_msg = await m.answer(f"{ce(E_BROADCAST_SEND, '📤')} Starting broadcast to {len(targets)} users...", parse_mode="HTML")

    sem = asyncio.Semaphore(20)

    async def _send_one(target_uid):
        async with sem:
            try:
                await m.copy_to(target_uid)
                return True
            except Exception:
                return False

    results = await asyncio.gather(*[_send_one(t) for t in targets])
    sent   = sum(1 for r in results if r is True)
    failed = len(targets) - sent

    await status_msg.edit_text(
        f"{ce(E_BROADCAST_DONE, '✅')} <b>Broadcast Complete!</b>\n\n"
        f"{ce(E_BROADCAST_SEND, '📨')} Sent  : <b>{sent}</b>\n"
        f"{ce(E_BROADCAST_FAIL, '❌')} Failed: <b>{failed}</b>\n"
        f"{ce(E_BROADCAST_USERS,'👥')} Total : <b>{len(targets)}</b>"
    )

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
    # Keep selections scoped to uploaded entries only.
    if service in state.demo_otp_services and country in state.demo_otp_countries:
        state.demo_otp_services.discard(service)
        state.demo_otp_countries.discard(country)
    else:
        state.demo_otp_services.add(service)
        state.demo_otp_countries.add(country)
    await c.answer()
    await safe_edit(c.message, "<b>Select</b>", demo_otp_selector_keyboard(pairs))


@dp.callback_query(F.data == "adm_demo_select_confirm")
async def adm_demo_select_confirm_cb(c: types.CallbackQuery):
    if not _admin_guard(c.from_user.id):
        await c.answer("❌ Access Denied!", show_alert=True); return
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
    if not (FORWARD_GROUP_ID or OTP_GROUP):
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

    print("🚀 Advanced SMS Bot Online — Ready!")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "chat_member"],
        polling_timeout=20,
        handle_signals=True,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    asyncio.run(main())
