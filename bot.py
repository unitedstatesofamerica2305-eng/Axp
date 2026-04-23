import asyncio
import logging
import random
import string
import re
from datetime import datetime, timedelta
from aiogram.types import CopyTextButton
from typing import List, Dict, Any, Optional
import pytz
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    PhotoSize
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import html
from aiogram.types import ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, KICKED, LEFT, RESTRICTED, MEMBER, ADMINISTRATOR, CREATOR
from aiogram.types import InputMediaPhoto

from typing import Union
# ---------------- CONFIGURATION ---------------- #
# ⚠️ CREDENTIALS
BOT_TOKEN = "8793837803:AAE1bMuqefil-e11f5fbxmbugrPBeSbuNlk" 
MONGO_URI = "mongodb+srv://unitedstatesofamerica2305_db_user:xzUhE91EUF8Zfi3l@cluster0.tu7bg5z.mongodb.net/?appName=Cluster0"
OWNER_IDS = [8322029867]
BOTUSER = "USERBOT"
ADMIN_IDS = [8322029867]
LOG_CHANNEL_ID = -1003764795977
WELCOME_IMAGE = "https://files.catbox.moe/27qumy.jpg"
VOTE_IM = "https://files.catbox.moe/mkfcpr.jpg"
PARTI_IMG = "https://files.catbox.moe/27qumy.jpg" #--participation image 
# TIMEZONE
IST = pytz.timezone('Asia/Kolkata')

# ---------------- LOGGING ---------------- #
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- DATABASE ---------------- #
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client['vespyr_votebot']
giveaways_col = db['giveaways']
votes_col = db['votes']
participants_col = db['participants']
users_col = db['users']
transactions_col = db['transactions']
# --- ADD TO DATABASE SECTION ---
settings_col = db['settings']  # Collection to store forced join channels
# --- ADD TO DATABASE SECTION ---
# ... existing collections ...
channels_col = db['channels'] # Stores channels where bot is admin


# ---------------- BOT & SCHEDULER SETUP ---------------- #
bot = Bot(
    token=BOT_TOKEN, 
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)
scheduler = AsyncIOScheduler(timezone=IST)
# --- DATABASE SECTION ---
start_settings_col = db['start_settings']

# --- MEMBERSHIP COLLECTIONS ---
membership_settings_col = db['membership_settings'] # Stores prices and QR
user_global_channels_col = db['user_global_channels'] # Stores user force-joins


# ---------------- STATES ---------------- #
class CreateGiveaway(StatesGroup):
    waiting_for_description = State()
    waiting_for_target_channel = State()
    waiting_for_target_link = State()
    # End Settings
    waiting_for_thumbnail = State() 
    waiting_for_extra_channel = State()
    waiting_for_end_type = State()
    waiting_for_end_time = State()
    # Paid Vote Settings
    waiting_for_paid_status = State()
    waiting_for_currency_type = State()
    waiting_for_inr_qr = State()
    waiting_for_star_username = State()
    waiting_for_rates = State() # Ask rate for both or single

class BuyVotes(StatesGroup):
    waiting_for_method = State()
    waiting_for_amount = State() # How much money/stars they sent
    waiting_for_proof = State()
# --- ADD TO STATES SECTION ---
class SetJoin(StatesGroup):
    waiting_for_input = State()

class SetStart(StatesGroup):
    waiting_for_text = State()
    
# --- NEW: POST MAKER STATE ---
class PostMaker(StatesGroup):
    waiting_for_media = State()
    waiting_for_caption = State()
    waiting_for_buttons = State()

# Update CreateGiveaway State to include new premium steps
    
# --- NEW STATES ---
class BuyMembership(StatesGroup):
    waiting_for_plan = State()
    waiting_for_proof = State()

class SetPrice(StatesGroup):
    waiting_for_input = State()

class AdminGift(StatesGroup):
    waiting_for_user = State()
    waiting_for_days = State()

class SetUserGlobal(StatesGroup):
    waiting_for_input = State()
    
    
# ---------------- HELPERS ---------------- #
def generate_id(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def is_user_member(user_id: int, channel_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except Exception:
        return False

def get_message_link(chat_username: str, chat_id: int, message_id: int) -> str:
    if chat_username:
        return f"https://t.me/{chat_username}/{message_id}"
    else:
        clean_id = str(chat_id).replace("-100", "")
        return f"https://t.me/c/{clean_id}/{message_id}"

async def get_membership(user_id: int):
    """Returns membership data if active, else None"""
    user = await users_col.find_one({"user_id": user_id})
    if not user or not user.get('membership_expiry'):
        return None
    
    expiry = user['membership_expiry']
    # Ensure expiry is timezone aware if stored that way, or make naive for comparison
    if expiry.tzinfo is None:
        expiry = IST.localize(expiry)
        
    if expiry > datetime.now(IST):
        return user
    return None

async def clean_expired_global_channels():
    """Removes user force-joins if membership expired"""
    # Find all user channels
    async for doc in user_global_channels_col.find({}):
        user = await get_membership(doc['user_id'])
        # If no membership or membership < 7 days (policy check), remove
        # logic: The prompt says removed after membership ends.
        if not user:
            await user_global_channels_col.delete_one({"_id": doc['_id']})
            


# ---------------- GLOBAL SCHEDULED RESYNC (2 MINUTE INTERVAL) ---------------- #
async def run_global_resync():
    """
    Runs periodically.
    Iterates through active giveaways, validates voter memberships, 
    removes invalid votes, and updates the UI with 'Must Join' buttons preserved.
    """
    logging.info("♻️ [Global Resync] Starting check cycle...")
    
    try:
        # 1. Find all ACTIVE giveaways
        async for ga in giveaways_col.find({"status": "active"}):
            ga_id = ga.get('ga_id')
            creator_id = ga.get('creator_id')
            target_id = ga.get('target_channel_id')
            if not ga_id or not target_id:
                continue

            # Identify all required channels (Target + Extras)
            required_channels = [{"id": target_id}]
            extras = ga.get('extra_channel') or ga.get('extra_channels')
            if extras:
                if isinstance(extras, list):
                    required_channels.extend(extras)
                elif isinstance(extras, dict):
                    required_channels.append(extras)

            # 2. Iterate through ALL votes for this giveaway
            async for vote in votes_col.find({"ga_id": ga_id}):
                voter_id = vote.get('voter_id')
                participant_id = vote.get('participant_id')
                
                if not voter_id or not participant_id:
                    continue

                is_valid_member = True
                voter_user_obj = None

                # Membership Check against all required channels
                for ch in required_channels:
                    try:
                        member = await bot.get_chat_member(chat_id=ch['id'], user_id=voter_id)
                        voter_user_obj = member.user 
                        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                            is_valid_member = False
                            break 
                    except Exception:
                        # If bot is kicked/not admin, we assume membership is okay for safety
                        continue
                
                await asyncio.sleep(0.05) # Rate limit protection

                # 3. Process Removal if User Left
                if not is_valid_member:
                    # DB Updates
                    await votes_col.delete_one({"_id": vote['_id']})
                    await participants_col.update_one(
                        {"ga_id": ga_id, "user_id": participant_id},
                        {"$inc": {"vote_count": -1}}
                    )
                    
                    # Fetch fresh data for UI/Logs
                    p_data = await participants_col.find_one({"ga_id": ga_id, "user_id": participant_id})
                    if not p_data: continue

                    voter_name = voter_user_obj.full_name if voter_user_obj else f"ID: {voter_id}"
                    new_count = p_data.get('vote_count', 0)

                    # --- A. UPDATE CHANNEL POST UI (Preserving Must-Join Buttons) ---
                    if p_data.get('msg_id'):
                        try:
                            chan_kb = InlineKeyboardBuilder()
                            
                            # Re-add "Must Join" Buttons for Voters
                            if extras:
                                if isinstance(extras, dict): extras = [extras]
                                for ch in extras:
                                    chan_kb.button(text=f"📢 Join {ch.get('title', 'Channel')}", url=ch['link'])
                            
                            # Update the Vote button with the new reduced count
                            chan_kb.button(text=f"🗳 Vote ({new_count})", callback_data=f"vote_{participant_id}_{ga_id}")
                            chan_kb.adjust(1)

                            await bot.edit_message_reply_markup(
                                chat_id=target_id,
                                message_id=p_data['msg_id'],
                                reply_markup=chan_kb.as_markup()
                            )
                        except Exception: pass

                    # --- B. SEND LOG TO TARGET CHANNEL (Auto-Delete) ---
                    try:
                        log_text = (
                            f"♻️ <b>Auto-Resync: Vote Removed</b>\n"
                            f"<blockquote>👤 <b>User:</b> {html.quote(voter_name)} left the channel.</blockquote>\n"
                            f"<blockquote>📉 <b>Participant:</b> {html.quote(p_data.get('name', 'Unknown'))}</blockquote>\n"
                            f"<blockquote>📰 Updated Votes: {new_count}</blockquote>"
                        )
                        log_msg = await bot.send_message(
                            chat_id=target_id,
                            text=log_text,
                            disable_notification=True
                        )
                        asyncio.create_task(delete_after_delay(target_id, log_msg.message_id, 60))
                    except Exception: pass

                    # --- C. NOTIFY PARTICIPANT (DM) ---
                    try:
                        p_dm = (
                            f"⚠️ <b>Vote Deduction Alert!</b>\n\n"
                            f"A user ({html.quote(voter_name)}) left the required channel.\n"
                            f"Your vote count has been reduced.\n"
                            f"📉 <b>New Count:</b> {new_count}"
                        )
                        await bot.send_message(chat_id=participant_id, text=p_dm)
                    except: pass

                    # --- D. NOTIFY CREATOR (DM) ---
                    try:
                        c_dm = (
                            f"🚨 <b>Voter Left - Vote Removed</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"👤 <b>Voter:</b> {html.quote(voter_name)}\n"
                            f"📉 <b>Affected:</b> {html.quote(p_data.get('name'))} (ID: {participant_id})\n"
                            f"📌 <b>Giveaway:</b> <code>{ga_id}</code>\n"
                            f"━━━━━━━━━━━━━━━━━━"
                        )
                        await bot.send_message(chat_id=creator_id, text=c_dm)
                    except: pass

    except Exception as e:
        logging.error(f"❌ Global Resync Error: {e}")

    logging.info("✅ [Global Resync] Cycle Complete.")
                

# ---------------- HELPER FOR AUTO-DELETE ---------------- #

async def delete_after_delay(chat_id: int, message_id: int, delay: int):
    """Deletes a message after the specified delay in seconds."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass
        

#----- set win
@router.message(Command("setwin"))
async def set_win_text_command(message: Message):
    # Security: Add your admin check here
    # if message.from_user.id not in ADMIN_IDS: return

    full_html = message.html_text
    
    try:
        _, new_template = full_html.split(maxsplit=1)
    except ValueError:
        await message.answer("⚠️ Please provide the text.\nExample: <code>/setwin 🏆 Winner is:\n\n{winners}</code>")
        return

    # VALIDATION: Ensure the admin included the {winners} tag
    if "{winners}" not in new_template:
        await message.answer(
            "❌ <b>Error:</b> You must include the <code>{winners}</code> placeholder in your text.\n"
            "This tells the bot where to list the users."
        )
        return

    # Save to DB
    await settings_col.update_one(
        {"_id": "global_win_template"}, 
        {"$set": {"text": new_template}}, 
        upsert=True
    )

    await message.answer(
        f"✅ <b>Win Message updated!</b>\n\n"
        f"<b>Preview of format:</b>\n{new_template}\n\n"
        f"<i>The bot will replace {{winners}} with the actual list.</i>"
    )
    

# ---------------- SCHEDULER LOGIC ---------------- #
async def auto_end_giveaway(ga_id: str):
    """Function called by scheduler to end giveaway automatically"""
    await end_giveaway_logic(ga_id, is_auto=True)

async def end_giveaway_logic(ga_id: str, is_auto: bool = False):
    ga = await giveaways_col.find_one({"ga_id": ga_id})
    if not ga or ga['status'] == 'ended':
        return

    # Update Status
    await giveaways_col.update_one({"ga_id": ga_id}, {"$set": {"status": "ended"}})
    
    # Calculate Winners
    top_participants = await participants_col.find({"ga_id": ga_id}).sort("vote_count", -1).limit(3).to_list(None)
    
    # --- GENERATE THE WINNERS LIST BLOCK ---
    # This creates the text block that replaces {winners}
    winners_text_block = ""
    if top_participants:
        for idx, p in enumerate(top_participants, 1):
            # We use html.quote to ensure weird names don't break the message format
            safe_name = html.quote(p['name'])
            winners_text_block += f"{idx}. {safe_name} - <b>{p['vote_count']} votes</b>\n"
    else:
        winners_text_block = "No participants found."

    # --- FETCH TEMPLATE ---
    settings = await settings_col.find_one({"_id": "global_win_template"})
    
    if settings and settings.get('text'):
        template = settings['text']
    else:
        # Default Fallback if admin never used /setwin
        template = (
            "🏆 <b>GIVEAWAY ENDED!</b> 🏆\n\n"
            "<b>🥇 Top 3 Winners:</b>\n"
            "{winners}\n\n"
            "<i>Thank you for participating!</i>"
        )

    # --- INJECT DATA ---
    # This puts the list exactly where the admin put {winners}
    final_caption = template.replace("{winners}", winners_text_block)

    # Post to Target Channel
    try:
        await bot.send_message(
            chat_id=ga['target_channel_id'], 
            text=final_caption,
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Failed to post results to channel: {e}")

    # Notify Creator
    try:
        creator_text = f"🚨 <b>Giveaway {ga_id} has ended {'automatically' if is_auto else 'manually'}.</b>\n\nResults posted to channel."
        await bot.send_message(chat_id=ga['creator_id'], text=creator_text)
    except:
        pass


# ---------------- COMMANDS & MENUS ---------------- #
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    # 1. Force Subscription Check (Skip for Owners)
    if message.from_user.id not in OWNER_IDS:
        if not await check_force_sub(message.from_user.id, message):
            return # Stop execution if they haven't joined
    
    # 2. Setup Variables
    args = command.args
    user = message.from_user
    
    # 3. Update User Database (Always runs on start)
    await users_col.update_one(
        {"user_id": user.id}, 
        {"$set": {"first_name": user.first_name, "username": user.username}}, 
        upsert=True
    )

    # 4. Handle Clean Start (No Arguments)
    if not args:
        # --- LOGGING: Notify Admin Channel ---
        try:
            # Make sure LOG_CHANNEL_ID is defined in your config
            await bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=f"<b>New User Started Bot</b>\n\n"
                     f"User: {user.mention_html()}\n"
                     f"ID: <code>{user.id}</code>\n\n"
                     f"• @{BOTUSER}"
            )
        except Exception:
            pass # Fail silently if ID is wrong or bot not admin there

        # --- CONTENT: Fetch Custom or Default Text ---
        custom_data = await start_settings_col.find_one({"type": "start_msg"})
        
        # If custom text exists, use it. Otherwise, use default.
        if custom_data and custom_data.get('text'):
            caption_text = custom_data['text']
        else:
            caption_text = (
                f"🤖 <b>ᴡᴇʟᴄᴏᴍᴇ {user.first_name}!</b> 🎁\n"
                "<blockquote expandable>✨ ꜰᴜʟʟʏ ᴀᴜᴛᴏᴍᴀᴛᴇᴅ & ꜰᴀɪʀ ɢɪᴠᴇᴀᴡᴀʏ ꜱʏꜱᴛᴇᴍ\n"
                "⚡ ꜰᴀꜱᴛ & ᴛʀᴀɴꜱᴘᴀʀᴇɴᴛ ᴡɪɴɴᴇʀ ꜱᴇʟᴇᴄᴛɪᴏɴ\n"
                "🛡️ ꜱᴇᴄᴜʀᴇ, ʀᴇʟɪᴀʙʟᴇ & ᴇᴀꜱʏ ᴛᴏ ᴜꜱᴇ\n"
                "🎉 ʜᴏꜱᴛ ɢɪᴠᴇᴀᴡᴀʏꜱ ᴡɪᴛʜ ᴀ ᴘʀᴇᴍɪᴜᴍ ᴇxᴘᴇʀɪᴇɴᴄᴇ</blockquote>\n"
                "<blockquote>• ᴛᴀᴘ ➕ ɴᴇᴡ ɢɪᴠᴇᴀᴡᴀʏ ʙᴜᴛᴛᴏɴ ᴛᴏ ᴄʀᴇᴀᴛᴇ ᴀ ɢɪᴠᴇᴀᴡᴀʏ.</blockquote>\n"
                "<blockquote>• ᴛᴀᴘ 🎁 ᴍʏ ɢɪᴠᴇᴀᴡᴀʏs ʙᴜᴛᴛᴏɴ ᴛᴏ ᴠɪᴇᴡ ʏᴏᴜʀ ɢɪᴠᴇᴀᴡᴀʏs.</blockquote>\n"
                "—————\n"
                "⚡ ᴘᴏᴡᴇʀᴇᴅ ʙʏ :- <a href='https://t.me/axpnet'>ᴠᴛʜ ɴᴇᴛᴡᴏʀᴋ</a>\n"
                "🛠️ ꜱᴜᴘᴘᴏʀᴛ :- <a href='https://t.me/PirateCodez'> Ƥ 𝛊 Ʀ ᧘ Ⲧ 𝛜</a>"
            )

        # -----------------------------

        kb = InlineKeyboardBuilder()
        kb.button(text=f"New Giveaway", callback_data="create_ga", style="primary", icon_custom_emoji_id="5409029744693897259")
        kb.button(text=f"My Giveaways", callback_data="my_ga", style="primary", icon_custom_emoji_id="5204046146955153467")
        kb.adjust(2)
        kb.button(text="How to Use", url="https://t.me/vthnet/27", style="primary", icon_custom_emoji_id="5269617636001460986")
        kb.adjust(1)
        kb.button(text="Add Channel", url=f"https://t.me/{BOTUSER}?startchannel=m&admin=post_messages+invite_users,startgroup=m&invite_users", style="primary", icon_custom_emoji_id="5397916757333654639")
        kb.button(text="Add Group", url=f"https://t.me/{BOTUSER}?startgroup=m&admin=invite_users", style="primary", icon_custom_emoji_id="5397916757333654639")
        kb.adjust(2)
        kb.button(text="Membership", callback_data="membership", style="danger", icon_custom_emoji_id="5949775417274536507")
        kb.adjust(1) 
        kb.button(text="Create Post", callback_data="create_post_start", style="success", icon_custom_emoji_id="6336811288437460963") 
        kb.adjust(2,1,2,1,1)
        # ... (Keep the rest of your keyboard buttons) ...
        await message.answer_photo(
            photo=WELCOME_IMAGE,
            has_spoiler=True,
            caption=caption_text, # <--- Uses the variable here
            reply_markup=kb.as_markup()
        )
        return
        

    # Participation Arg: {ga_id}
    await handle_participation_flow(message, user, args)

@router.callback_query(F.data == "back_to_start")
async def back_to_start(call: CallbackQuery):
    await call.message.delete()
    await cmd_start(call.message, CommandObject(prefix="/", command="start", args=None))

# ---------------- GIVEAWAY CREATION FLOW ---------------- #
async def check_force_sub(user_id: int, message: Message = None):
    # 1. Get Admin Settings
    settings = await settings_col.find_one({"type": "force_join"})
    channels = settings.get('channels', []) if settings else []
    
    # 2. Get Active User Global Channels (Premium Feature)
    # We fetch valid user channels from DB
    async for u_ch in user_global_channels_col.find({}):
        # Double check expiry just in case scheduler hasn't run
        mem = await get_membership(u_ch['user_id'])
        if mem:
            channels.append(u_ch['channel'])

    if not channels:
        return True
    
    # 3. Check membership
    missing_channels = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch['id'], user_id=user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                missing_channels.append(ch)
        except Exception:
            pass
            
    if not missing_channels:
        return True

    if message:
        kb = InlineKeyboardBuilder()
        for ch in missing_channels:
            # Check if 'link' exists, otherwise provide a fallback or skip
            link = ch.get('link', '')
            if link:
                kb.button(text="📢 Join Channel", url=link)
        
        kb.adjust(2,1)
        kb.button(text="✅ Verify Join", callback_data="verify_bot_fsub", style="primary")
        
        await message.answer(
            "🛑 <b>Access Denied</b>\n\n"
            "To use this bot, you must join our official channels first.",
            reply_markup=kb.as_markup()
        )
    return False
    

@router.callback_query(F.data == "verify_bot_fsub")
async def verify_bot_fsub(call: CallbackQuery):
    # Pass 'None' as message so it doesn't resend the block msg, we handle UI here
    is_joined = await check_force_sub(call.from_user.id, message=None) 
    
    if is_joined:
        await call.message.delete()
        await call.message.answer("✅ <b>Verified!</b> Type /start to continue.")
        # Optional: You could trigger cmd_start manually here
    else:
        await call.answer("❌ You haven't joined all channels yet!", show_alert=True)

#-------
# ---------------- CREATE GIVEAWAY FLOW (ENHANCED) ---------------- #
# --- 4. TARGET CHANNEL (SMART SELECTOR) ---
async def ask_target_channel(message: Union[Message, CallbackQuery], state: FSMContext, page: int = 0):
    """
    Rewritten Target Channel logic with a Professional Paginated Selector.
    Replaces the manual prompt with a list of verified admin channels.
    """
    user_id = message.from_user.id
    ITEMS_PER_PAGE = 5
    
    # 1. Fetch Potential Channels (Database + History)
    unique_chats = {} 
    async for ch in channels_col.find({"added_by": user_id}):
        unique_chats[ch['chat_id']] = ch['title']

    async for ga in giveaways_col.find({"creator_id": user_id}):
        c_id = ga.get('target_channel_id')
        if c_id and c_id not in unique_chats:
            unique_chats[c_id] = ga.get('target_channel_title', "Recent Channel")

    # 2. Validation (Verify Bot is Admin)
    valid_chats = []
    for ch_id, title in unique_chats.items():
        try:
            bot_m = await bot.get_chat_member(ch_id, bot.id)
            if bot_m.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                valid_chats.append({'id': ch_id, 'title': title})
        except:
            continue

    # 3. Pagination Math
    total_pages = (len(valid_chats) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(0, min(page, total_pages - 1)) if total_pages > 0 else 0
    start, end = page * ITEMS_PER_PAGE, (page + 1) * ITEMS_PER_PAGE
    current_batch = valid_chats[start:end]

    # 4. Build Professional UI
    kb = InlineKeyboardBuilder()
    
    # List Valid Channels
    for chat in current_batch:
        kb.button(text=f"📢 {chat['title']}", callback_data=f"sel_target_{chat['id']}", style="primary")
    kb.adjust(1)

    # Navigation Row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"pg_target_{page-1}", style="danger"))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"pg_target_{page+1}", style="success"))
    if nav_row:
        kb.row(*nav_row)

    # Manual Entry & Back Logic
    kb.row(InlineKeyboardButton(text="✍️ Enter Manually", callback_data="man_target", style="primary"))
    
    mem = await get_membership(user_id)
    back_cb = "back_to_extras" if mem else "back_to_desc"
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data=back_cb))

    # Text Content
    header = "🎯 <b>Select Target Channel</b>"
    desc = (
        "Choose the channel where the giveaway will be posted.\n"
        "<i>Only channels where I am an Admin are shown below.</i>"
    )
    final_text = f"{header}\n\n{desc}\n\n<b>Found:</b> {len(valid_chats)} Channels"

    # 5. Render (Edit if Callback, Answer if Message)
    if isinstance(message, Message):
        await message.answer(final_text, reply_markup=kb.as_markup())
    else:
        try:
            await message.edit_text(final_text, reply_markup=kb.as_markup())
        except:
            await message.message.answer(final_text, reply_markup=kb.as_markup())
            
    
# --- 1. START & DESCRIPTION ---
@router.callback_query(F.data == "create_ga")
async def start_create_ga(call: CallbackQuery, state: FSMContext):
    await state.clear()
    
    text = (
        "📝 <b>Create New Giveaway: Step 1</b>\n\n"
        "<b>Enter Giveaway Description</b>\n"
        "Send a short, catchy title for your event.\n"
        "<i>(e.g., 'iPhone 15 Contest', 'Best Photo 2024')</i>\n\n"
        "<blockquote>💡 Type /skip to use default: 'Vote for your favorite!'</blockquote>"
    )
    
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Cancel", callback_data="back_to_start", style="danger")
    
    await call.message.edit_caption(caption=text, reply_markup=kb.as_markup())
    await state.set_state(CreateGiveaway.waiting_for_description)

@router.message(CreateGiveaway.waiting_for_description)
async def set_desc(message: Message, state: FSMContext):
    desc = message.text.strip() if message.text != "/skip" else "Vote for your favorite!"
    if len(desc) > 200:
        await message.answer("⚠️ Description is too long. Please keep it under 200 characters.")
        return

    await state.update_data(description=desc)
    
    # Check Membership for Premium Features
    mem = await get_membership(message.from_user.id)
    
    if mem:
        # PREMIUM FLOW
        text = (
            "🖼 <b>Custom Thumbnail (Premium)</b>\n\n"
            "Send an image to use as the banner for this giveaway.\n"
            "This makes your post look more professional.\n\n"
            "<blockquote>Type /skip to use the default bot image.</blockquote>"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Back", callback_data="back_to_desc", style="danger")
        
        await message.answer(text, reply_markup=kb.as_markup())
        await state.set_state(CreateGiveaway.waiting_for_thumbnail)
    else:
        await ask_target_channel(message, state)
    

# --- BACK HANDLER FOR DESCRIPTION ---
@router.callback_query(F.data == "back_to_desc")
async def back_to_desc_handler(call: CallbackQuery, state: FSMContext):
    await start_create_ga(call, state)


# --- 2. THUMBNAIL (PREMIUM ONLY) ---
# ---------------- SMART CHANNEL SELECTOR LOGIC ---------------- #

async def render_channel_selector(message: Union[Message, CallbackQuery], state: FSMContext, page: int, mode: str):
    """
    Renders a professional UI listing channels where the user and bot are admins.
    mode: 'target' or 'extra'
    """
    user_id = message.from_user.id
    ITEMS_PER_PAGE = 5
    
    # 1. Fetch potential channels from DB (History + Added via Event)
    unique_chats = {} 

    # Source A: Channels where bot was added (channels_col)
    async for ch in channels_col.find({"added_by": user_id}):
        unique_chats[ch['chat_id']] = ch['title']

    # Source B: Giveaway History
    async for ga in giveaways_col.find({"creator_id": user_id}):
        c_id = ga.get('target_channel_id')
        if c_id and c_id not in unique_chats:
            unique_chats[c_id] = ga.get('target_channel_title', str(c_id))

    # 2. Validation (Live Check)
    valid_chats = []
    all_ids = sorted(unique_chats.keys())

    for ch_id in all_ids:
        try:
            # Check Bot Admin
            bot_member = await bot.get_chat_member(ch_id, bot.id)
            if bot_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                continue
            # Check User Admin
            user_member = await bot.get_chat_member(ch_id, user_id)
            if user_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                continue
                
            valid_chats.append({'id': ch_id, 'title': unique_chats[ch_id]})
        except:
            continue

    # 3. Pagination Logic
    total_items = len(valid_chats)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    if page >= total_pages: page = max(0, total_pages - 1)
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_batch = valid_chats[start_idx:end_idx]

    # 4. Build Professional UI
    kb = InlineKeyboardBuilder()
    
    # Channel Buttons (1 column for readability)
    for chat in current_batch:
        kb.button(text=f"📢 {chat['title']}", callback_data=f"sel_{mode}_{chat['id']}")
    kb.adjust(1)

    # Navigation Row
    nav_btns = []
    if page > 0:
        nav_btns.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"pg_{mode}_{page-1}"))
    if total_pages > 1:
        nav_btns.append(InlineKeyboardButton(text=f"📄 {page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav_btns.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"pg_{mode}_{page+1}"))
    if nav_btns:
        kb.row(*nav_btns)

    # Action Buttons
   
    if mode == "extra":
        kb.row(InlineKeyboardButton(text="⏭ Skip Extra Channel", callback_data="skip_extra"))
    
    # Back Button
    back_cb = "back_to_desc" if mode == "extra" else "back_to_extras"
    if mode == "target" and not (await get_membership(user_id)):
        back_cb = "back_to_desc" # Fix for free users skipping extra
        
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data=back_cb))

    # Text Content
    if mode == "target":
        header = "📢 <b>Select Target Channel</b>"
        desc = "Choose where the giveaway post will be published.\n<i>I must be an admin there!</i>"
    else:
        header = "➕ <b>Select Extra Channel</b> (Optional)"
        desc = "Users must join this channel to vote.\n<i>Limit: 1 Channel (Premium)</i>"

    final_text = f"{header}\n\n{desc}"
    
    # Send or Edit
    if isinstance(message, Message):
        await message.answer(final_text, reply_markup=kb.as_markup())
    else:
        try:
            await message.message.edit_text(final_text, reply_markup=kb.as_markup())
        except:
            await message.message.delete()
            await message.message.answer(final_text, reply_markup=kb.as_markup())

# ---------------- NAVIGATION HANDLERS (PAGINATION) ---------------- #

@router.callback_query(F.data.startswith("pg_"))
async def handle_selector_pagination(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    mode = parts[1] # target or extra
    page = int(parts[2])
    await render_channel_selector(call, state, page, mode)

@router.callback_query(F.data == "ignore")
async def ignore_callback(call: CallbackQuery):
    await call.answer()

# ---------------- THUMBNAIL (ENTRY POINT) ---------------- #

@router.message(CreateGiveaway.waiting_for_thumbnail)
async def set_ga_thumbnail(message: Message, state: FSMContext):
    if message.photo:
        await state.update_data(custom_thumb=message.photo[-1].file_id)
    elif message.text == "/skip":
        await state.update_data(custom_thumb=None)
    else:
        await message.answer("❌ Please send a <b>Photo</b> or type <code>/skip</code>.")
        return

    # Reset extra channels
    await state.update_data(extra_channels=[])
    
    # Start Extra Channel Selection (Premium Only)
    # The calling logic ensures only premium users get here
    await render_channel_selector(message, state, 0, "extra")


# ---------------- EXTRA CHANNEL LOGIC (REWRITTEN) ---------------- #

# 1. Selection Handler (Auto Link Generation)
@router.callback_query(F.data.startswith("sel_extra_"))
async def select_extra_channel(call: CallbackQuery, state: FSMContext):
    ch_id = int(call.data.split("_")[2])
    
    try:
        chat = await bot.get_chat(ch_id)
        # Try to get existing link or export one
        link = chat.invite_link
        if not link:
            link = await bot.export_chat_invite_link(ch_id)
            
        # Save as single dict (Requirement: Max 1 channel)
        # We store it in a list to keep compatibility with your DB schema if it expects a list
        channel_data = [{"id": ch_id, "link": link, "title": chat.title}]
        await state.update_data(extra_channels=channel_data)
        
        await call.answer("✅ Channel Selected!")
        await ask_target_channel_flow(call, state)
        
    except Exception as e:
        await call.answer(f"❌ Error generating link: {e}", show_alert=True)

# 2. Skip Handler
@router.callback_query(F.data == "skip_extra")
async def skip_extra_channel(call: CallbackQuery, state: FSMContext):
    await state.update_data(extra_channels=[])
    await ask_target_channel_flow(call, state)

# 3. Manual Entry Prompt
@router.callback_query(F.data == "man_extra")
async def manual_extra_prompt(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "📝 <b>Enter Extra Channel Manually</b>\n\n"
        "Format: <code>ChannelID InviteLink</code>\n"
        "Example: <code>-10012345678 https://t.me/...</code>\n\n"
        "<i>Note: I must be an admin there!</i>",
        reply_markup=InlineKeyboardBuilder().button(text="🔙 Back", callback_data="back_to_extra_list").as_markup()
    )
    await state.set_state(CreateGiveaway.waiting_for_extra_channel)

@router.callback_query(F.data == "back_to_extra_list")
async def back_to_extra_list(call: CallbackQuery, state: FSMContext):
    await render_channel_selector(call, state, 0, "extra")

# 4. Manual Entry Processing
@router.message(CreateGiveaway.waiting_for_extra_channel)
async def process_manual_extra(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split()
        if len(parts) < 2: raise ValueError
        
        ex_id = int(parts[0])
        ex_link = parts[1]
        
        # Verify
        m = await bot.get_chat_member(ex_id, bot.id)
        if m.status != ChatMemberStatus.ADMINISTRATOR:
            await message.answer("⚠️ I am not admin there.")
            return
            
        chat = await bot.get_chat(ex_id)
        
        # Save
        channel_data = [{"id": ex_id, "link": ex_link, "title": chat.title}]
        await state.update_data(extra_channels=channel_data)
        
        await message.answer("✅ <b>Extra Channel Added!</b>")
        await ask_target_channel_flow(message, state) # Proceed to Target
        
    except:
        await message.answer("❌ Invalid format. Use: <code>ID Link</code>")


# ---------------- TARGET CHANNEL LOGIC (REWRITTEN) ---------------- #

# Helper to route correctly (Free vs Premium back button handling handled in render)
async def ask_target_channel_flow(event, state):
    await render_channel_selector(event, state, 0, "target")

# 1. Selection Handler
@router.callback_query(F.data.startswith("sel_target_"))
async def select_target_channel(call: CallbackQuery, state: FSMContext):
    ch_id = int(call.data.split("_")[2])
    
    try:
        chat = await bot.get_chat(ch_id)
        
        # Generate Link Logic
        link = chat.invite_link
        if not link:
            link = await bot.export_chat_invite_link(ch_id)
            
        # Save Data directly (Skipping the "Send Link" step since we generated it)
        await state.update_data(
            target_channel_id=chat.id,
            target_channel_title=chat.title,
            target_channel_username=chat.username,
            target_link=link
        )
        
        # Proceed straight to End Type (Skip manual link input)
        await ask_end_configuration(call.message, state)
        
    except Exception as e:
        await call.answer(f"❌ Error getting link: {e}", show_alert=True)

# 2. Manual Entry Prompt
@router.callback_query(F.data == "man_target")
async def manual_target_prompt(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "📢 <b>Enter Target Channel Manually</b>\n\n"
        "Send the <b>Username</b> (e.g., @mychannel) or <b>ID</b>.",
        reply_markup=InlineKeyboardBuilder().button(text="🔙 Back", callback_data="back_to_target_list").as_markup()
    )
    await state.set_state(CreateGiveaway.waiting_for_target_channel)

@router.callback_query(F.data == "back_to_target_list")
async def back_to_target_list(call: CallbackQuery, state: FSMContext):
    await render_channel_selector(call, state, 0, "target")

# 3. Manual Entry Processing (ID/Username)
@router.message(CreateGiveaway.waiting_for_target_channel)
async def set_channel_manual(message: Message, state: FSMContext):
    try:
        chat = await bot.get_chat(message.text.strip())
        if not await is_user_member(bot.id, chat.id):
             await message.answer("❌ I am not admin there.")
             return
        
        await state.update_data(
            target_channel_id=chat.id, 
            target_channel_title=chat.title, 
            target_channel_username=chat.username
        )
        
        await message.answer(
            f"✅ <b>Selected:</b> {chat.title}\n\n"
            "🔗 <b>Send Channel Invite Link</b>\n"
            "Send the public invite link."
        )
        await state.set_state(CreateGiveaway.waiting_for_target_link)
    except:
        await message.answer("❌ Channel not found.")

# Back handler for the Manual Link step
@router.callback_query(F.data == "back_to_target_select")
async def back_to_target_select(call: CallbackQuery, state: FSMContext):
    await render_channel_selector(call, state, 0, "target")


# ---------------- TRANSITION TO END CONFIG ---------------- #

async def ask_end_configuration(message: Message, state: FSMContext):
    text = (
        "⏳ <b>Giveaway Ending Configuration</b>\n\n"
        "<b>🤖 Automatic:</b> Ends automatically at a specific time.\n"
        "<b>✋ Manual:</b> You stop it manually using the panel."
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🤖 Automatic End", callback_data="end_auto", style="success")
    kb.button(text="✋ Manual End", callback_data="end_manual", style="danger")
    kb.adjust(2)
    kb.button(text="🔙 Back", callback_data="back_to_target_select")
    
    # Check if message is editable
    if isinstance(message, Message):
        # If it's a new message (from manual input), send new
        try:
            await message.edit_text(text, reply_markup=kb.as_markup())
        except:
            await message.answer(text, reply_markup=kb.as_markup())
    else:
        # If it's a callback, edit
        await message.edit_text(text, reply_markup=kb.as_markup())
        
    await state.set_state(CreateGiveaway.waiting_for_end_type)

# Fix for the "Back" button in End Type to return to Target Selector
@router.callback_query(F.data == "back_to_extras")
async def back_to_extras_router(call: CallbackQuery, state: FSMContext):
    # This handles the back button from Target List to Extra List
    await render_channel_selector(call, state, 0, "extra")


    


# --- hhjhjjjjjj--
@router.callback_query(CreateGiveaway.waiting_for_end_type)
async def set_end_type(call: CallbackQuery, state: FSMContext):
    mode = call.data
    await state.update_data(end_mode=mode)
    
    if mode == "end_manual":
        # If Manual, go straight to Paid Votes config
        # We pass the 'call' object so ask_paid_votes knows to EDIT the message
        await ask_paid_votes(call, state)
    else:
        # If Auto, ask for the Date/Time
        now_str = datetime.now(IST).strftime("%d-%m-%Y %H:%M")
        text = (
            "📅 <b>Set End Date & Time</b>\n\n"
            f"Current Time (IST): <code>{now_str}</code>\n\n"
            "<b>Format:</b> <code>DD-MM-YYYY HH:MM</code>\n"
            "<i>Example:</i> <code>25-12-2025 18:00</code>"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Back", callback_data="back_to_end_type")
        
        await call.message.edit_text(text, reply_markup=kb.as_markup())
        await state.set_state(CreateGiveaway.waiting_for_end_time)

@router.callback_query(F.data == "back_to_end_type")
async def back_to_end_type(call: CallbackQuery, state: FSMContext):
    # Returns to the Auto/Manual selection screen
    # Note: ask_end_configuration must be defined in your code (as per previous steps)
    await ask_end_configuration(call.message, state)

@router.message(CreateGiveaway.waiting_for_end_time)
async def set_end_time(message: Message, state: FSMContext):
    try:
        dt_str = message.text.strip()
        dt_naive = datetime.strptime(dt_str, "%d-%m-%Y %H:%M")
        dt_ist = IST.localize(dt_naive)
        
        if dt_ist <= datetime.now(IST):
            await message.answer("❌ <b>Error:</b> Time must be in the future.")
            return

        await state.update_data(end_date_iso=dt_ist.isoformat())
        
        # Confirm time
        await message.answer(f"✅ Will end on: <b>{dt_ist.strftime('%d %b %Y, %I:%M %p IST')}</b>")
        
        # PROCEED TO PAID VOTES
        # We pass the 'message' object so ask_paid_votes knows to SEND A NEW message
        await ask_paid_votes(message, state)
        
    except ValueError:
        await message.answer("❌ <b>Invalid Format.</b>\nPlease use: <code>DD-MM-YYYY HH:MM</code>")

# --- 6. PAID VOTES CONFIGURATION (ROBUST FIX) ---

async def ask_paid_votes(event: Union[Message, CallbackQuery], state: FSMContext):
    text = (
        "💰 <b>Paid Votes Configuration</b>\n\n"
        "Do you want to allow users to buy extra votes using Money or Telegram Stars?\n"
        "<i>This generates revenue and increases vote counts.</i>"
    )
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Enable Paid Votes", callback_data="paid_yes", style="success")
    kb.button(text="❌ Disable Paid Votes", callback_data="paid_no", style="primary")
    kb.adjust(1) 
    kb.button(text="🔙 Back", callback_data="back_to_end_type", style="danger")
    
    # Check if the event is a Callback (Button Click) or Message (Text Input)
    if isinstance(event, CallbackQuery):
        # Came from "Manual End" button -> Edit the previous message
        await event.message.edit_text(text, reply_markup=kb.as_markup())
    elif isinstance(event, Message):
        # Came from "Time Input" text -> Send a new message
        await event.answer(text, reply_markup=kb.as_markup())
    else:
        # Fallback for edge cases
        try:
            await event.message.edit_text(text, reply_markup=kb.as_markup())
        except:
            await event.answer(text, reply_markup=kb.as_markup())
        
    await state.set_state(CreateGiveaway.waiting_for_paid_status)
    


########
@router.callback_query(CreateGiveaway.waiting_for_paid_status)
async def set_paid_status(call: CallbackQuery, state: FSMContext):
    await call.answer()
    
    if call.data == "paid_no":
        await call.message.edit_text("⏳ <i>Processing giveaway without paid votes...</i>")
        
        # CHANGE: We pass 'user_from_call=call.from_user' so the bot knows YOU clicked it
        await finalize_giveaway(call.message, state, paid_enabled=False, user_from_call=call.from_user)
        
    elif call.data == "paid_yes":
        await state.update_data(paid_enabled=True)
        text = "💱 <b>Select Supported Currency</b>\n\nChoose how you want to receive payments:"
        kb = InlineKeyboardBuilder()
        kb.button(text="🇮🇳 INR (UPI/QR)", callback_data="curr_inr", style="primary")
        kb.button(text="⭐️ Telegram Stars", callback_data="curr_star", style="primary")
        kb.button(text="🔄 Both (INR & Stars)", callback_data="curr_both", style="primary")
        kb.adjust(1)
        kb.button(text="🔙 Back", callback_data="back_to_paid_ask", style="danger")
        
        await call.message.edit_text(text, reply_markup=kb.as_markup())
        await state.set_state(CreateGiveaway.waiting_for_currency_type)
        
    elif call.data == "back_to_end_type":
        # Returns user to the Automatic/Manual selection
        data = await state.get_data()
        text = "⏳ <b>Ending Configuration</b>\n\nHow should this giveaway end?"
        kb = InlineKeyboardBuilder()
        kb.button(text="🤖 Automatic End", callback_data="end_auto")
        kb.button(text="✋ Manual End", callback_data="end_manual")
        kb.adjust(2)
        await call.message.edit_text(text, reply_markup=kb.as_markup())
        await state.set_state(CreateGiveaway.waiting_for_end_type)
        

########
@router.callback_query(F.data == "back_to_paid_ask")
async def back_to_paid_ask(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await ask_paid_votes(call.message, state)

@router.callback_query(CreateGiveaway.waiting_for_currency_type)
async def set_currency(call: CallbackQuery, state: FSMContext):
    await call.answer()
    ctype = call.data
    await state.update_data(currency_type=ctype)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Back", callback_data="back_to_paid_ask")

    if ctype in ["curr_inr", "curr_both"]:
        await call.message.edit_text(
            "📸 <b>Upload Payment QR Code</b>\n\n"
            "Please send the <b>Photo</b> of your UPI/QR Code now.",
            reply_markup=kb.as_markup()
        )
        await state.set_state(CreateGiveaway.waiting_for_inr_qr)
    elif ctype == "curr_star":
        await call.message.edit_text(
            "👤 <b>Telegram Star Recipient</b>\n\n"
            "Enter the @username where users should send Stars.",
            reply_markup=kb.as_markup()
        )
        await state.set_state(CreateGiveaway.waiting_for_star_username)

@router.message(CreateGiveaway.waiting_for_inr_qr)
async def set_qr(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ <b>Error:</b> Please send a Photo/Image of your QR code.")
        return
    
    file_id = message.photo[-1].file_id
    await state.update_data(qr_code=file_id)
    
    data = await state.get_data()
    if data['currency_type'] == "curr_both":
        await message.answer("👤 <b>Now enter Telegram @Username for Stars:</b>")
        await state.set_state(CreateGiveaway.waiting_for_star_username)
    else:
        await ask_rates(message, state)

@router.message(CreateGiveaway.waiting_for_star_username)
async def set_star_user(message: Message, state: FSMContext):
    username = message.text.strip()
    if not username.startswith("@"):
        username = "@" + username
    await state.update_data(star_user=username)
    await ask_rates(message, state)

async def ask_rates(message: Message, state: FSMContext):
    data = await state.get_data()
    ctype = data.get('currency_type')
    
    text = "📊 <b>Set Vote Rates</b>\n\n"
    if ctype == "curr_inr":
        text += "How many votes for <b>1 INR</b>?\n<i>Example: Send 10 (user gets 10 votes per 1 Rupee)</i>"
    elif ctype == "curr_star":
        text += "How many votes for <b>1 Star</b>?\n<i>Example: Send 5 (user gets 5 votes per 1 Star)</i>"
    else:
        text += (
            "Enter rates for both <b>INR</b> and <b>Stars</b>.\n"
            "<b>Format:</b> <code>INR_RATE STAR_RATE</code>\n"
            "<i>Example: Send 10 20</i>"
        )
    
    await message.answer(text)
    await state.set_state(CreateGiveaway.waiting_for_rates)

@router.message(CreateGiveaway.waiting_for_rates)
async def set_rates(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    ctype = data.get('currency_type')
    rates = {}
    
    try:
        if ctype == "curr_inr":
            rates['inr'] = int(text)
        elif ctype == "curr_star":
            rates['star'] = int(text)
        else:
            parts = text.split()
            if len(parts) != 2: raise ValueError
            rates['inr'] = int(parts[0])
            rates['star'] = int(parts[1])
            
        await state.update_data(rates=rates)
        
        # Success message before finalizing
        await message.answer("✅ <b>Rates recorded!</b> Finalizing your giveaway...")
        await finalize_giveaway(message, state, paid_enabled=True)
        
    except ValueError:
        await message.answer("❌ <b>Invalid Input:</b> Please enter numbers only.\n<i>Example: 10</i>")

# --- BACK NAVIGATION FIX ---
@router.callback_query(F.data == "back_to_currency")
async def back_to_currency_selection(call: CallbackQuery, state: FSMContext):
    await call.answer()
    # Reset to the Selection screen
    await set_paid_status(call, state)
    
# --- 7. FINALIZATION & ADMIN LOGGING ---
async def finalize_giveaway(message: Message, state: FSMContext, paid_enabled: bool, user_from_call=None):
    try:
        data = await state.get_data()
        user = user_from_call if user_from_call else message.from_user
        ga_id = generate_id() # Ensure this function exists in your code
        
        # --- 1. PREPARE DATA WITH FALLBACKS ---
        # This prevents the bot from freezing if a specific state key is missing
        description = data.get('description', "Vote for your favorite!")
        target_id = data.get('target_channel_id')
        target_link = data.get('target_link', "https://t.me/telegram")
        target_title = data.get('target_channel_title', "Channel")
        target_user = data.get('target_channel_username')
        end_mode = data.get('end_mode', "end_manual")
        custom_thumb = data.get('custom_thumb')
        
        # Handle Extra Channels (List support)
        # We look for 'extra_channels' (the new list) or 'extra_channel' (the old single dict)
        extras = data.get('extra_channels') or data.get('extra_channel')
        
        # --- 2. MEMBERSHIP STATUS FOR LOGS ---
        user_mem = await get_membership(user.id)
        mem_status = "Premium 💎" if user_mem else "Free 👤"

        # --- 3. CREATE DATABASE DOCUMENT ---
        doc = {
            "ga_id": ga_id,
            "creator_id": user.id,
            "description": description,
            "target_channel_id": target_id,
            "target_channel_title": target_title,
            "target_channel_username": target_user,
            "target_link": target_link,
            "end_mode": end_mode,
            "status": "active",
            "created_at": datetime.now(),
            "participants_count": 0,
            "paid_enabled": paid_enabled,
            "custom_thumb": custom_thumb,
            "extra_channel": extras, # Stored as a list of dicts
        }

        # Handle Auto-End Timing
        if end_mode == "end_auto" and data.get('end_date_iso'):
            doc['end_time'] = data['end_date_iso']
            dt = datetime.fromisoformat(data['end_date_iso'])
            # Schedule the task
            scheduler.add_job(
                auto_end_giveaway, 
                'date', 
                run_date=dt, 
                args=[ga_id], 
                id=f"job_{ga_id}",
                replace_existing=True
            )
            doc['job_id'] = f"job_{ga_id}"

        # Handle Paid Settings
        if paid_enabled:
            doc.update({
                "currency_type": data.get('currency_type'),
                "qr_code": data.get('qr_code'),
                "star_user": data.get('star_user'),
                "rates": data.get('rates', {})
            })

        # --- 4. SAVE TO DATABASE ---
        await giveaways_col.insert_one(doc)

        # --- 5. SEND ADMIN LOGS ---
        auto_txt = "Manual"
        if data.get('end_date_iso'):
            auto_txt = datetime.fromisoformat(data['end_date_iso']).strftime('%d-%b %H:%M')
            
        log_text = (
            f"🆕 <b>New Giveaway Created</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> {html.quote(user.full_name)}\n"
            f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
            f"💎 <b>Status:</b> {mem_status}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Paid:</b> {'Yes ✅' if paid_enabled else 'No ❌'}\n"
            f"⏳ <b>End:</b> {auto_txt}\n"
            f"🆔 <b>GA-ID:</b> <code>{ga_id}</code>"
        )
        try:
            await bot.send_message(chat_id=-1003764795977, text=log_text)
        except: pass

        # --- 6. FINAL SUCCESS MESSAGE TO USER ---
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start={ga_id}"
        
        kb = InlineKeyboardBuilder()
        kb.button(text="⚙️ Manage Giveaway", callback_data=f"manage_ga_{ga_id}", style="success")
        kb.button(text="🏆 Leaderboard", callback_data=f"leaderboard_{ga_id}", style="primary")
        kb.adjust(1)

        await message.answer(
            f"✅ <b>Giveaway Created Successfully!</b>\n\n"
            f"📝 <b>Desc:</b> {description}\n"
            f"🆔 <b>ID:</b> <code>{ga_id}</code>\n\n"
            f"🔗 <b>Participation Link:</b>\n{link}",
            reply_markup=kb.as_markup()
        )
        
        # Clear state ONLY after everything is successful
        await state.clear()

    except Exception as e:
        # If it freezes, this will tell you WHY
        logging.error(f"Error in finalize_giveaway: {e}")
        await message.answer(f"❌ <b>Creation Failed:</b>\n<code>{str(e)}</code>")
        
    # -----------------------------

    
# --- ADMIN SETSTART HANDLERS ---
@router.message(Command("setstart"))
async def cmd_setstart(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    await message.answer("📝 <b>Send the new Start Message.</b>\n\nI will preserve all formatting (bold, italic, links, etc.).\nUse /resetstart to go back to default.")
    await state.set_state(SetStart.waiting_for_text)

@router.message(SetStart.waiting_for_text)
async def process_setstart(message: Message, state: FSMContext):
    # .html_text preserves the EXACT formatting sent by the user
    new_text = message.html_text
    await start_settings_col.update_one(
        {"type": "start_msg"},
        {"$set": {"text": new_text}},
        upsert=True
    )
    await message.answer("✅ <b>Start message updated successfully!</b>")
    await state.clear()

@router.message(Command("resetstart"))
async def cmd_resetstart(message: Message):
    if message.from_user.id not in OWNER_IDS: return
    await start_settings_col.delete_one({"type": "start_msg"})
    await message.answer("🔄 <b>Start message reset to default.</b>")
    

@router.message(Command("resync"))
async def resync_votes(message: Message, command: CommandObject):
    """
    Checks all voters in a giveaway. If they left the channel, removes vote.
    Usage: /resync {ga_id}
    """
    
    # 1. Validation
    ga_id = command.args
    if not ga_id:
        await message.answer("⚠️ Usage: <code>/resync {giveaway_id}</code>")
        return

    ga = await giveaways_col.find_one({"ga_id": ga_id})
    if not ga:
        await message.answer("❌ Invalid Giveaway ID.")
        return

    
@router.message(Command("resync"))
async def resync_votes(message: Message, command: CommandObject):
    ga_id = command.args
    if not ga_id:
        await message.answer("⚠️ Usage: <code>/resync {giveaway_id}</code>")
        return

    ga = await giveaways_col.find_one({"ga_id": ga_id})
    if not ga:
        await message.answer("❌ Invalid Giveaway ID.")
        return

    # STRICT CHECK: Only Creator
    if message.from_user.id != ga['creator_id']:
         await message.answer("❌ Only the giveaway creator can use /resync.")
         return

    status_msg = await message.answer("⏳ <b>Manual Resync Started...</b>\n<i>Validating all voters...</i>")
    
    # Define required channels based on your schema
    req_channels = [ga['target_channel_id']]
    if ga.get('extra_channel'):
        req_channels.append(ga['extra_channel']['id'])

    removed_count = 0
    affected_participants = {} 

    votes_cursor = votes_col.find({"ga_id": ga_id})
    async for vote in votes_cursor:
        voter_id = vote['voter_id']
        is_still_member = True
        
        for ch_id in req_channels:
            try:
                member = await bot.get_chat_member(chat_id=ch_id, user_id=voter_id)
                if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                    is_still_member = False
                    break
            except:
                continue # Skip error if bot can't check

        if not is_still_member:
            await votes_col.delete_one({"_id": vote['_id']})
            removed_count += 1
            
            await participants_col.update_one(
                {"ga_id": ga_id, "user_id": vote['participant_id']},
                {"$inc": {"vote_count": -1}}
            )
            affected_participants[vote['participant_id']] = True

    # Batch Update UI
    if removed_count > 0:
        for p_id in affected_participants.keys():
            p_data = await participants_col.find_one({"ga_id": ga_id, "user_id": p_id})
            if p_data and p_data.get("msg_id"):
                try:
                    kb = InlineKeyboardBuilder()
                    kb.button(text="Join Channel", url=ga['target_link'], style="primary")
                    if ga.get('extra_channel'):
                        kb.button(text=f"Join", url=ga['extra_channel']['link'], style="primary")
                    kb.adjust(1)
                    kb.button(text=f"🗳 Vote ({p_data['vote_count']})", callback_data=f"vote_{p_id}_{ga_id}", style="success")
                    kb.adjust(1, 1)
                    await bot.edit_message_reply_markup(chat_id=ga['target_channel_id'], message_id=p_data['msg_id'], reply_markup=kb.as_markup())
                except: pass

    await status_msg.edit_text(f"✅ <b>Resync Done!</b>\n\n🗑 Removed: {removed_count} votes.")
    

# ---------------- PARTICIPATION ---------------- #

    
#---------VoteText
# --- 1. The Admin Command to Set Text ---
@router.message(Command("setvotetext"))
async def set_vote_text_command(message: Message):
    # Check if user is admin (Add your own admin check logic here if needed)
    # if message.from_user.id not in ADMIN_IDS: return
    
    # We use html_text to preserve Bold, Italic, Links, Spoilers etc exactly as sent
    full_html = message.html_text
    
    # Remove the command '/setvotetext' from the string to get the caption
    # We split by maxsplit 1 to keep the rest of the message intact
    try:
        _, new_template = full_html.split(maxsplit=1)
    except ValueError:
        await message.answer("⚠️ Please provide the text after the command.\nExample: <code>/setvotetext My Caption...</code>")
        return

    # Store in a global settings collection
    await settings_col.update_one(
        {"_id": "global_vote_caption"}, 
        {"$set": {"text": new_template}}, 
        upsert=True
    )

    await message.answer(f"✅ <b>Vote text updated successfully!</b>\n\n<b>Preview:</b>\n{new_template}")

# ---------------- PARTICIPATION LOGIC (UPGRADED) ---------------- #

# --- 1. Main Entry Point ---
# ---------------- PARTICIPATION & REGISTRATION LOGIC ---------------- #

# --- 1. Helper: Channel Verification ---
async def get_missing_channels(user_id: int, ga: dict) -> list:
    """
    Checks Target + Extra Channels.
    Returns a list of missing channel dicts {id, link, title}.
    """
    required = []
    
    # Add Target Channel (Primary requirement)
    required.append({
        "id": ga['target_channel_id'], 
        "link": ga['target_link'], 
        "title": ga.get('target_channel_title', 'Main Channel')
    })
    
    # Add Extra Channels (Must-Joins set by Premium creators)
    extras = ga.get('extra_channel') or ga.get('extra_channels')
    if extras:
        if isinstance(extras, list):
            required.extend(extras)
        elif isinstance(extras, dict):
            required.append(extras)
            
    missing = []
    for ch in required:
        try:
            member = await bot.get_chat_member(chat_id=ch['id'], user_id=user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                missing.append(ch)
        except Exception:
            # Assume missing if bot is not admin or can't reach channel
            missing.append(ch)
            
    return missing

# --- 2. Main Entry: Handle Participation Flow ---
async def handle_participation_flow(message: Message, user, ga_id):
    ga = await giveaways_col.find_one({"ga_id": ga_id})
    
    if not ga or ga['status'] != 'active':
        await message.answer("⚠️ <b>Giveaway Inactive</b>\nThis event has ended or is no longer available.")
        return

    # Check if user is already a participant
    if await participants_col.find_one({"ga_id": ga_id, "user_id": user.id}):
        await send_ga_links(message, user, ga_id)
        return

    # Verify if user has joined all required channels
    missing = await get_missing_channels(user.id, ga)

    if missing:
        kb = InlineKeyboardBuilder()
        for ch in missing:
            kb.button(text=f"📢 Join {ch.get('title', 'Channel')}", url=ch['link'])
            
        kb.adjust(1)
        kb.button(text="✅ I Have Joined", callback_data=f"verify_{ga_id}")
        
        await message.answer(
            "👋 <b>Welcome!</b>\n\nTo enter this giveaway, you must join the required channels below first.",
            reply_markup=kb.as_markup()
        )
        return

    # If all channels joined, move to confirmation
    await ask_confirmation(message, ga)

# --- 3. Verification & Confirmation UI ---
@router.callback_query(F.data.startswith("verify_"))
async def verify_callback(call: CallbackQuery):
    ga_id = call.data.split("_")[1]
    ga = await giveaways_col.find_one({"ga_id": ga_id})
    
    if await get_missing_channels(call.from_user.id, ga):
        await call.answer("❌ You still haven't joined all channels!", show_alert=True)
        return
        
    await call.message.delete()
    await ask_confirmation(call.message, ga)

async def ask_confirmation(message: Message, ga):
    """Sends the professional confirmation prompt."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔥 Confirm & Participate", callback_data=f"confirm_join_{ga['ga_id']}", style="success")
    kb.button(text="❌ Cancel", callback_data="delete_msg", style="danger")
    
    await message.answer(
        f"💎 <b>Verification Successful</b>\n\n"
        f"<b>Event:</b> {html.quote(ga.get('description', 'Giveaway'))}\n\n"
        f"Ready to generate your personal vote post in the target channel?",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("confirm_join_"))
async def confirm_participation_callback(call: CallbackQuery):
    ga_id = call.data.split("_")[2]
    ga = await giveaways_col.find_one({"ga_id": ga_id})
    
    # Final check
    if await get_missing_channels(call.from_user.id, ga):
        await call.answer("⚠️ Session Expired. Please re-verify memberships.", show_alert=True)
        return

    await call.message.delete()
    await register_participant(call.message, call.from_user, ga)

# --- 4. Registration Logic (Vote Post Creation) ---
async def register_participant(message: Message, user, ga):
    # Prepare the Keyboard for the Channel Post
    chan_kb = InlineKeyboardBuilder()
    
    # DYNAMIC: Add "Must Join" Buttons for Voters if creator set them
    extras = ga.get('extra_channel') or ga.get('extra_channels')
    if extras:
        if isinstance(extras, dict): extras = [extras]
        for ch in extras:
            chan_kb.button(text=f"📢 Join", url=ch['link'], style="primary")
    
    # Add the Vote Button at the bottom
    chan_kb.button(text="🗳 Vote (0)", callback_data=f"vote_{user.id}_{ga['ga_id']}", style="success")
    chan_kb.adjust(1)

    # Caption Processing
    settings = await settings_col.find_one({"_id": "global_vote_caption"})
    template = settings.get('text') if settings else "<b>⚡ PARTICIPANT:</b> {user.full_name}\n<b>ID:</b> {user.id}"

    class FormatUser:
        def __init__(self, u):
            self.full_name = html.quote(u.full_name)
            self.id = u.id
            self.username = u.username if u.username else "NoUser"

    try:
        caption = template.replace(" or 'NoUser'", "").format(user=FormatUser(user))
    except:
        caption = f"⚡ <b>Participant:</b> {html.quote(user.full_name)}"

    try:
        sent = await bot.send_photo(
            chat_id=ga['target_channel_id'],
            photo=ga.get('custom_thumb') or VOTE_IM, 
            caption=caption,
            reply_markup=chan_kb.as_markup()
        )
    except Exception as e:
        await message.answer(f"❌ <b>Error:</b> Could not post to target channel.\n{e}")
        return

    # Database Entry
    await participants_col.insert_one({
        "ga_id": ga['ga_id'],
        "user_id": user.id,
        "username": user.username,
        "name": user.full_name,
        "vote_count": 0,
        "paid_votes_count": 0,
        "msg_id": sent.message_id,
        "channel_id": ga['target_channel_id']
    })
    await giveaways_col.update_one({"ga_id": ga['ga_id']}, {"$inc": {"participants_count": 1}})

    await send_ga_links(message, user, ga['ga_id'])



# --- 5. Success Menu UI ---
async def send_ga_links(message: Message, user, ga_id):
    ga = await giveaways_col.find_one({"ga_id": ga_id})
    p_data = await participants_col.find_one({"ga_id": ga_id, "user_id": user.id})
    
    if not p_data: 
        return

    # Construct the deep link to the specific message in the channel
    post_link = get_message_link(ga.get('target_channel_username'), ga['target_channel_id'], p_data['msg_id'])
    
    # --- PREPARE THE TEXT TO BE COPIED ---
    copy_content = (
        f"🔥 Vote for me in the Giveaway!\n\n"
        f"📢 Channel: {ga['target_link']}\n"
        f"🗳 Post Link: {post_link}\n\n"
        f"⚠️ Note: Don't leave from any channel, @Vthvotebot uses automatic votes resync system!"
    )

    # --- MAIN UI MESSAGE (CAPTION) ---
    text = (
        f"🎊 <b>Participation Confirmed!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 <b>Target Channel:</b> <a href='{ga['target_link']}'>Open Channel</a>\n"
        f"🗳 <b>Your Vote Post:</b> <a href='{post_link}'>View My Post</a>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ <i>Tip: Click the button below to copy your referral details and share with friends!</i>"
    )
    
    kb = InlineKeyboardBuilder()
    
    # 1. THE COPY BUTTON (Using copy_text feature)
    kb.row(
        InlineKeyboardButton(
            text="Copy Vote Link",
            copy_text=CopyTextButton(text=copy_content)
        )
    )
    
    # 2. ADDITIONAL ACTIONS
    if ga.get('paid_enabled'):
        kb.row(InlineKeyboardButton(text="💰 Buy Paid Votes", callback_data=f"buy_start_{ga_id}", style="success"))
    
    kb.row(InlineKeyboardButton(text="🏆 Leaderboard", callback_data=f"leaderboard_{ga_id}", style="primary"))
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="🔄 Get Links Again", callback_data=f"get_links_{ga_id}", style="primary"))
    kb.adjust(1,1,1)
    # --- SEND PHOTO WITH CAPTION ---
    # Note: 'disable_web_page_preview' is removed because it is not valid for answer_photo
    
    if isinstance(message, Message):
        await message.answer_photo(
            photo=PARTI_IMG,
            has_spoiler=True,
            caption=text,
            reply_markup=kb.as_markup()
        )
    else:
        # If called from a callback, message is extracted but treated the same way
        await message.answer_photo(
            photo=PARTI_IMG,
            has_spoiler=True,
            caption=text,
            reply_markup=kb.as_markup()
        )

@router.callback_query(F.data.startswith("get_links_"))
async def callback_get_links(call: CallbackQuery):
    ga_id = call.data.split("_")[2]
    # We answer the callback to stop the loading animation
    await call.answer("🔄 Refreshing your links...")
    # We call the function passing the message object from the callback
    await send_ga_links(call.message, call.from_user, ga_id)
    

    

#--------setprices
@router.message(Command("setprices"))
async def cmd_setprices(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    
    # Expected format: /setprices 1D 20 7D 70 30D 80
    args = message.text.split()[1:]
    if not args or len(args) % 2 != 0:
        await message.answer("⚠️ Usage: <code>/setprices 1D 20 7D 70 30D 80</code>\n(DayCount Price DayCount Price...)")
        return
    
    plans = []
    try:
        for i in range(0, len(args), 2):
            label = args[i].upper() # 1D
            price = args[i+1]       # 20
            
            # Extract days
            days = int(label.replace('D', ''))
            plans.append({"label": label, "days": days, "price": price})
            
        await membership_settings_col.update_one(
            {"type": "plans"}, 
            {"$set": {"plans": plans}}, 
            upsert=True
        )
        await message.answer("✅ <b>Membership Plans Updated!</b>")
        
    except ValueError:
        await message.answer("❌ Error: Days must be numbers (e.g. 1D) and Price must be numbers.")

@router.message(Command("setqr"))
async def cmd_setqr(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.answer("⚠️ Reply to a photo to set it as the Payment QR.")
        return
        
    file_id = message.reply_to_message.photo[-1].file_id
    await membership_settings_col.update_one(
        {"type": "qr"}, 
        {"$set": {"file_id": file_id}}, 
        upsert=True
    )
    await message.answer("✅ <b>QR Code Updated!</b>")

#--------MEMBRRSHUP

@router.message(Command("setmemtext"))
async def set_membership_text(message: Message):
    # 1. Security Check (Replace with your actual Admin ID check)
    if message.from_user.id not in ADMIN_IDS: 
        return

    # 2. Check if replying to a message
    if not message.reply_to_message:
        await message.answer(
            "⚠️ <b>How to use:</b>\n"
            "1. Write a message with the exact formatting (Bold, Italic, Emojis) you want.\n"
            "2. Include <code>{status}</code> where you want the Active/Inactive info to appear.\n"
            "3. <b>Reply</b> to that message with <code>/setmemtext</code>."
        )
        return

    # 3. Get the HTML representation of the replied message
    # .html_text preserves all formatting (bold, links, spoilers, etc.)
    custom_html = message.reply_to_message.html_text
    
    # 4. Save to Database
    await membership_settings_col.update_one(
        {"type": "ui_text"},
        {"$set": {"membership_msg": custom_html}},
        upsert=True
    )

    response_text = "✅ <b>Membership text updated!</b>"
    
    # Warning if they forgot the placeholder
    if "{status}" not in custom_html:
        response_text += "\n\n⚠️ <b>Note:</b> You didn't include <code>{status}</code> in your text. The user's active/expired status will NOT be visible."

    await message.answer(response_text)
    

#------Assuming you have these imports from your existing code
@router.message(Command("membership"))
@router.callback_query(F.data == "membership")
async def cmd_membership(event: Union[Message, CallbackQuery]):
    user_id = event.from_user.id
    user_mem = await get_membership(user_id)
    
    # --- STATUS LOGIC (UNCHANGED) ---
    status_text = "❌ <b>Inactive</b>"
    kb = InlineKeyboardBuilder()
    
    if user_mem:
        expiry = user_mem['membership_expiry']
        if expiry.tzinfo is None:
            expiry = IST.localize(expiry)
        now_ist = datetime.now(IST)
        
        if expiry > now_ist:
            expiry_str = expiry.strftime('%d-%b-%Y %I:%M %p IST')
            days_left = (expiry - now_ist).days
            status_text = f"✅ <b>Active</b>\n📅 Expires: {expiry_str}\n⏳ Remaining: {days_left} days"
            
            if days_left >= 28:
                 kb.button(text="📢 Set Global Channel(Premium Mode)", callback_data="set_user_global_sub")
        else:
            status_text = "❌ <b>Expired</b>"

    # --- TEXT GENERATION (UPDATED) ---
    
    # 1. Fetch custom text from DB
    ui_settings = await membership_settings_col.find_one({"type": "ui_text"})
    
    # 2. Define Default Fallback (If admin hasn't set anything yet)
    default_template = (
        "💎 <b>PREMIUM MEMBERSHIP</b> 💎\n\n"
        "<b>Status:</b> {status}\n\n"
        "❤️‍🔥 <b>Features:</b>\n"
        "• 🖼 <b>Custom Thumbnail</b> for Giveaways\n"
        "• ⚡ <b>Access /resync</b> (Fix votes)\n"
        "• ➕ <b>Extra Force-Join</b> (Per Giveaway)\n"
        "• 👑 <b>Set Global Force-Join</b> (7D+ Plans)\n\n"
        "✨ <i>Choose a plan to upgrade:</i>"
    )

    # 3. Select template
    if ui_settings and ui_settings.get("membership_msg"):
        template = ui_settings['membership_msg']
    else:
        template = default_template

    # 4. Inject the status variable safely
    # We use .replace instead of .format to avoid errors if the admin used curly braces elsewhere
    final_text = template.replace("{status}", status_text)
    
    # --- BUTTONS & SENDING (UPDATED FOR IMAGE/CALLBACK) ---
    
    settings = await membership_settings_col.find_one({"type": "plans"})
    if settings and settings.get('plans'):
        for plan in settings['plans']:
            kb.button(text=f"{plan['label']} - ₹{plan['price']}", callback_data=f"buy_mem_{plan['days']}_{plan['price']}")
            
    kb.button(text="🔙 Back", callback_data="back_to_start")
    kb.adjust(1)
    
    if isinstance(event, Message):
        await event.answer(final_text, reply_markup=kb.as_markup())
    
    elif isinstance(event, CallbackQuery):
        await event.answer()
        
        # Handle editing Logic (Photo vs Text)
        if event.message.photo:
            try:
                # Try editing caption (Limit: 1024 chars)
                await event.message.edit_caption(caption=final_text, reply_markup=kb.as_markup())
            except Exception:
                # If text is too long for caption, or type mismatch, delete and resend
                await event.message.delete()
                await event.message.answer(final_text, reply_markup=kb.as_markup())
        else:
            await event.message.edit_text(final_text, reply_markup=kb.as_markup())
                                          
        
@router.callback_query(F.data.startswith("buy_mem_"))
async def buy_mem_start(call: CallbackQuery, state: FSMContext):
    days = int(call.data.split("_")[2])
    price = call.data.split("_")[3]
    
    # Get QR
    qr_data = await membership_settings_col.find_one({"type": "qr"})
    if not qr_data:
        await call.answer("❌ Payments not configured.", show_alert=True)
        return
        
    await state.update_data(plan_days=days, plan_price=price)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ I've Paid", callback_data="mem_paid_confirm")
    kb.button(text="🔙 Cancel", callback_data="delete_msg")
    
    await call.message.answer_photo(
        photo=qr_data['file_id'],
        caption=f"💳 <b>Purchase {days} Days Membership</b>\n\n💸 Amount: <b>₹{price}</b>\n\nScan and pay exactly this amount.",
        reply_markup=kb.as_markup()
    )
    await state.set_state(BuyMembership.waiting_for_proof)
    await call.answer()

@router.callback_query(F.data == "mem_paid_confirm", BuyMembership.waiting_for_proof)
async def mem_ask_proof(call: CallbackQuery):
    await call.message.edit_caption(caption="📸 <b>Upload Screenshot</b>\n\nPlease send the transaction screenshot now.")

@router.message(BuyMembership.waiting_for_proof)
async def mem_process_proof(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Please send an image.")
        return
        
    data = await state.get_data()
    days = data['plan_days']
    
    # Notify Admin
    kb = InlineKeyboardBuilder()
    # Format: approve_mem_USERID_DAYS
    kb.button(text="✅ Approve", callback_data=f"aprmem_{message.from_user.id}_{days}")
    kb.button(text="❌ Reject", callback_data=f"rejmem_{message.from_user.id}")
    kb.adjust(2)
    
    caption = (
        f"💎 <b>New Membership Purchase</b>\n\n"
        f"👤 User: {message.from_user.mention_html()}\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"📅 Plan: {days} Days\n"
        f"💸 Price: ₹{data['plan_price']}"
    )
    
    # Send to Owner(s) - sending to first owner for simplicity or loop
    for admin in OWNER_IDS:
        try:
            await bot.send_photo(
                chat_id=admin, 
                photo=message.photo[-1].file_id, 
                caption=caption, 
                reply_markup=kb.as_markup()
            )
        except: pass
        
    await message.answer("✅ <b>Proof Sent!</b> Waiting for admin approval.")
    await state.clear()

# --- ADMIN APPROVAL HANDLERS ---
@router.callback_query(F.data.startswith("aprmem_"))
async def approve_membership(call: CallbackQuery):
    _, user_id_str, days_str = call.data.split("_")
    user_id = int(user_id_str)
    days = int(days_str)
    
    # Calculate Time
    current_mem = await get_membership(user_id)
    now = datetime.now(IST)
    
    if current_mem:
        # Extend
        new_expiry = current_mem['membership_expiry'] + timedelta(days=days)
        msg_type = "extended"
    else:
        # New
        new_expiry = now + timedelta(days=days)
        msg_type = "activated"
        
    # Update DB
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"membership_expiry": new_expiry, "membership_level": "premium"}},
        upsert=True
    )
    
    date_fmt = new_expiry.strftime('%d-%b-%Y %I:%M %p IST')
    
    # Notify User
    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Payment Approved!</b>\n\n"
            f"💎 {days} Days Membership {msg_type}.\n"
            f"📅 <b>Valid till:</b> {date_fmt}\n\n"
            f"<i>Type /membership to manage.</i>"
        )
    except: pass
    
    # Log
    log_text = (
        f"💎 <b>Membership Active</b>\n"
        f"User: <a href='tg://user?id={user_id}'>{user_id}</a>\n"
        f"Plan: {days} Days\n"
        f"Ends: {date_fmt}"
        f"<b>Features </b>:"
    )
    try:
        await bot.send_message(LOG_CHANNEL_ID, log_text)
    except: pass
    
    await call.message.edit_caption(caption=call.message.caption + "\n\n✅ <b>APPROVED</b>")

@router.callback_query(F.data.startswith("rejmem_"))
async def reject_membership(call: CallbackQuery):
    user_id = int(call.data.split("_")[1])
    try:
        await bot.send_message(user_id, "❌ Your membership request was rejected.")
    except: pass
    await call.message.edit_caption(caption=call.message.caption + "\n\n❌ <b>REJECTED</b>")


#-------- GIFT
@router.message(Command("gift"))
async def cmd_gift(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS: return
    await message.answer("🎁 <b>Gift Membership</b>\n\nSend the User ID.")
    await state.set_state(AdminGift.waiting_for_user)

@router.message(AdminGift.waiting_for_user)
async def gift_get_user(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
        await state.update_data(target_id=uid)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="1 Day", callback_data="gift_1")
        kb.button(text="7 Days", callback_data="gift_7")
        kb.button(text="30 Days", callback_data="gift_30")
        
        await message.answer("⏳ <b>Select Duration</b>", reply_markup=kb.as_markup())
    except:
        await message.answer("❌ Invalid ID.")

@router.callback_query(F.data.startswith("gift_"))
async def gift_confirm(call: CallbackQuery, state: FSMContext):
    days = int(call.data.split("_")[1])
    data = await state.get_data()
    user_id = data['target_id']
    
    now = datetime.now(IST)
    new_expiry = now + timedelta(days=days)
    
    # Check existing to extend
    current = await get_membership(user_id)
    if current:
        new_expiry = current['membership_expiry'] + timedelta(days=days)
    
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"membership_expiry": new_expiry}},
        upsert=True
    )
    
    await call.message.edit_text(f"✅ Gifted {days} days to <code>{user_id}</code>")
    try:
        await bot.send_message(user_id, f"🎁 <b>You received a gift!</b>\n\n💎 {days} Days Membership added by Admin.")
    except: pass
    await state.clear()

@router.message(Command("conmembership"))
async def cmd_conmembership(message: Message):
    if message.from_user.id not in OWNER_IDS: return
    
    # Find active members
    now = datetime.now(IST)
    cursor = users_col.find({"membership_expiry": {"$gt": now}})
    members = await cursor.to_list(None)
    
    if not members:
        await message.answer("❌ No active memberships.")
        return
        
    text = "💎 <b>Active Memberships</b>\n\n"
    kb = InlineKeyboardBuilder()
    
    for m in members:
        expiry = m['membership_expiry'].strftime('%d-%b')
        name = m.get('first_name', 'User')
        btn_text = f"{name} ({expiry})"
        kb.button(text=btn_text, callback_data=f"view_mem_{m['user_id']}")
        
    kb.adjust(1)
    await message.answer(text, reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("view_mem_"))
async def view_member_details(call: CallbackQuery):
    user_id = int(call.data.split("_")[2])
    user = await users_col.find_one({"user_id": user_id})
    
    if not user:
        await call.answer("User not found", show_alert=True)
        return
        
    expiry = user.get('membership_expiry')
    if not expiry:
        text = "❌ Membership Expired."
    else:
        text = (
            f"👤 <b>User Details</b>\n"
            f"ID: <code>{user_id}</code>\n"
            f"Name: {user.get('first_name')}\n"
            f"📅 Expires: {expiry.strftime('%d-%b-%Y %H:%M')}\n"
        )
        
    kb = InlineKeyboardBuilder()
    kb.button(text="🚫 Cancel Membership", callback_data=f"cancel_mem_{user_id}")
    kb.button(text="🔙 Back", callback_data="delete_msg")
    
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("cancel_mem_"))
async def cancel_membership(call: CallbackQuery):
    user_id = int(call.data.split("_")[2])
    await users_col.update_one({"user_id": user_id}, {"$unset": {"membership_expiry": ""}})
    await call.message.edit_text("✅ Membership Cancelled.")
    


@router.callback_query(F.data == "set_user_global_sub")
async def start_set_user_global(call: CallbackQuery, state: FSMContext):
    # Security check again
    mem = await get_membership(call.from_user.id)
    if not mem: 
        await call.answer("❌ Membership expired", show_alert=True)
        return

    await call.message.answer(
        "👑 <b>Set Global Force-Join Channel</b>\n\n"
        "Send Channel ID and Link.\nFormat: <code>-100xxxxx https://t.me/...</code>\n\n"
        "⚠️ Bot must be Admin there!\nℹ️ Replaces any previous channel set by you."
    )
    await state.set_state(SetUserGlobal.waiting_for_input)
    await call.answer()

@router.message(SetUserGlobal.waiting_for_input)
async def process_user_global(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split()
        if len(parts) != 2: raise ValueError
        
        ch_id = int(parts[0])
        link = parts[1]
        
        # Verify Bot Admin
        try:
            m = await bot.get_chat_member(ch_id, bot.id)
            if m.status != ChatMemberStatus.ADMINISTRATOR:
                await message.answer("❌ Bot is not admin in that channel.")
                return
        except:
             await message.answer("❌ Can't access channel. Make sure ID is correct and I am added.")
             return

        # Save
        doc = {
            "user_id": message.from_user.id,
            "channel": {"id": ch_id, "link": link, "title": "Sponsored Channel"}
        }
        
        # Update/Replace
        await user_global_channels_col.update_one(
            {"user_id": message.from_user.id},
            {"$set": doc},
            upsert=True
        )
        
        await message.answer("✅ <b>Global Channel Set!</b>\nIt will be active as long as your membership is valid.")
        await state.clear()
        
    except:
        await message.answer("❌ Invalid format. Use: <code>ID LINK</code>")
        
# ---------------- VOTING (ORGANIC) ---------------- #


# Assuming router, database collections, and get_missing_channels are defined elsewhere

@router.callback_query(F.data.startswith("vote_"))
async def handle_channel_vote(call: CallbackQuery):
    """
    Handles voting logic for organic giveaways.
    Enforces STRICT single vote per giveaway policy.
    Format: vote_{participant_id}_{ga_id}
    """
    # --- 1. Parsing & Basic Validation ---
    try:
        parts = call.data.split("_")
        if len(parts) < 3: raise ValueError
        participant_id_str, ga_id = parts[1], parts[2]
        participant_id = int(participant_id_str)
    except (ValueError, IndexError):
        await call.answer("❌ Invalid vote data structure.", show_alert=True)
        return

    voter = call.from_user

    # --- 2. Fetch Giveaway Status ---
    ga = await giveaways_col.find_one({"ga_id": ga_id})
    if not ga or ga.get('status') != 'active':
        await call.answer("❌ This Giveaway has ended or is inactive.", show_alert=True)
        return

    # --- 3. Self-Vote Check ---
    if voter.id == participant_id:
        await call.answer("⚠️ ᴏᴘᴇʀᴀᴛɪᴏɴ ᴅᴇɴɪᴇᴅ\n\nʏᴏᴜ ᴄᴀɴɴᴏᴛ ᴠᴏᴛᴇ ғᴏʀ ʏᴏᴜʀsᴇʟғ!", show_alert=True)
        return

    # --- 4. STRICT SINGLE VOTE CHECK ---
    # We check if the voter has voted for ANYONE in this specific giveaway (ga_id)
    existing_vote = await votes_col.find_one({
        "ga_id": ga_id, 
        "voter_id": voter.id
    })
    
    if existing_vote:
        # Check if they are clicking the same person or a new person
        if existing_vote['participant_id'] == participant_id:
            msg = "❌ ʏᴏᴜ ʜᴀᴠᴇ ᴀʟʀᴇᴀᴅʏ ᴠᴏᴛᴇᴅ ғᴏʀ ᴛʜɪs ᴘᴀʀᴛɪᴄɪᴘᴀɴᴛ."
        else:
            # They voted for someone else previously
            msg = (
                "⚠️ ᴠᴏᴛᴇ ʟɪᴍɪᴛ ʀᴇᴀᴄʜᴇᴅ\n\n"
                "🚫 ʏᴏᴜ ᴄᴀɴ ᴏɴʟʏ ᴠᴏᴛᴇ ғᴏʀ ᴏɴᴇ ᴘᴀʀᴛɪᴄɪᴘᴀɴᴛ ɪɴ ᴛʜɪs ɢɪᴠᴇᴀᴡᴀʏ.\n"
                "ʏᴏᴜ ᴄᴀɴɴᴏᴛ ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ᴠᴏᴛᴇ."
            )
        await call.answer(msg, show_alert=True)
        return

    # --- 5. Verify Target Participant Exists ---
    participant = await participants_col.find_one({"ga_id": ga_id, "user_id": participant_id})
    if not participant:
        await call.answer("❌ Error: Participant record not found.", show_alert=True)
        return

    # --- 6. Force Join Check (Target + Extra Channels) ---
    missing = await get_missing_channels(voter.id, ga)
    if missing:
        # Create a detailed alert message listing missing channels
        channel_names = "\n".join([f"• {ch.get('title', 'Required Channel')}" for ch in missing])
        alert_msg = (
            "🚫 ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ\n\n"
            "ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴛʜᴇ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟs ʙᴇғᴏʀᴇ ᴠᴏᴛɪɴɢ:\n"
            f"{channel_names}\n\n"
            "👉 ᴜsᴇ ᴛʜᴇ ᴊᴏɪɴ ʙᴜᴛᴛᴏɴs ᴀʙᴏᴠᴇ ᴛʜɪs ᴘᴏsᴛ!"
        )
        await call.answer(alert_msg, show_alert=True)
        return

    # --- 7. Process Vote (Database Operations) ---
    # Insert Vote Record
    await votes_col.insert_one({
        "ga_id": ga_id,
        "voter_id": voter.id,
        "participant_id": participant_id,
        "voted_at": datetime.now()
    })
    
    # Increment Participant Vote Count Atomically
    await participants_col.update_one(
        {"_id": participant['_id']},
        {"$inc": {"vote_count": 1}}
    )
    
    # --- 8. UI UPDATE: Dynamic Button Refresh ---
    new_count = participant.get('vote_count', 0) + 1
    chan_kb = InlineKeyboardBuilder()
    
    # Re-generate the Extra Channel buttons so they don't disappear
    extras = ga.get('extra_channel') or ga.get('extra_channels')
    if extras:
        if isinstance(extras, dict): extras = [extras]
        for ch in extras:
            chan_kb.button(text="📢 Join", url=ch['link'])
    
    # Add the updated Vote button
    chan_kb.button(text=f"🗳 Vote ({new_count})", callback_data=call.data)
    chan_kb.adjust(1)
    
    try:
        await call.message.edit_reply_markup(reply_markup=chan_kb.as_markup())
    except TelegramBadRequest:
        # Prevents crashing if the markup is identical (user spammed click)
        pass
    except Exception as e:
        print(f"Error updating markup: {e}")
    
    # --- 9. Success Alert (Enhanced) ---
    alert_text = (
        f"[✅] ᴠᴏᴛᴇ ᴀᴅᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ\n\n"
        f"‣ ᴠᴏᴛᴇ ғʀᴏᴍ : {voter.full_name}\n"
        f"‣ ɴᴇᴡ ᴄᴏᴜɴᴛ : {new_count}\n"
        f"‣ ᴠᴏᴛᴇᴅ ғᴏʀ : {participant.get('name', 'Participant')}\n"
        f"‣ ʙᴏᴛ : @Vthvotebot"
    )
    await call.answer(alert_text, show_alert=True)
    

# ---------------- PAID VOTES FLOW ---------------- #

@router.callback_query(F.data.startswith("buy_start_"))
async def buy_start(call: CallbackQuery, state: FSMContext):
    ga_id = call.data.split("_")[2]
    ga = await giveaways_col.find_one({"ga_id": ga_id})
    
    if ga['status'] != 'active':
        await call.answer("Giveaway ended.", show_alert=True)
        return

    # Determine Method
    methods = []
    if ga.get('currency_type') in ['curr_inr', 'curr_both']: methods.append('inr')
    if ga.get('currency_type') in ['curr_star', 'curr_both']: methods.append('star')
    
    await state.update_data(ga_id=ga_id, ga_doc=ga)

    if len(methods) > 1:
        kb = InlineKeyboardBuilder()
        kb.button(text="🇮🇳 INR (QR)", callback_data="pay_method_inr")
        kb.adjust(1)
        kb.button(text="⭐️ Stars", callback_data="pay_method_star")
        await call.message.answer("💳 <b>Select Payment Method:</b>", reply_markup=kb.as_markup())
        await state.set_state(BuyVotes.waiting_for_method)
    else:
        await process_payment_display(call.message, state, methods[0])
    
    await call.answer()

@router.callback_query(BuyVotes.waiting_for_method)
async def payment_method_selected(call: CallbackQuery, state: FSMContext):
    method = call.data.split("_")[2] # inr or star
    await process_payment_display(call.message, state, method)

async def process_payment_display(message: Message, state: FSMContext, method: str):
    data = await state.get_data()
    ga = data['ga_doc']
    rates = ga['rates']
    
    await state.update_data(selected_method=method)
    
    info_text = ""
    if method == "inr":
        rate = rates.get('inr')
        info_text = f"🇮🇳 <b>Pay via QR</b>\n\nRate: <b>{rate} Votes / 1 INR</b>\n\n1. Scan QR below.\n2. Pay desired amount.\n3. Send Screenshot here."
        if ga.get('qr_code'):
            await message.answer_photo(photo=ga['qr_code'], caption=info_text)
        else:
            await message.answer(info_text)
    else:
        rate = rates.get('star')
        target_user = ga.get('star_user')
        info_text = f"⭐️ <b>Pay via Stars</b>\n\nRate: <b>{rate} Votes / 1 Star</b>\n\n1. Send stars to {target_user}.\n2. Send Screenshot of transaction here."
        await message.answer(info_text)
        
    await state.set_state(BuyVotes.waiting_for_proof)

@router.message(BuyVotes.waiting_for_proof)
async def receive_proof(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Please send a screenshot image.")
        return
        
    data = await state.get_data()
    ga = data['ga_doc']
    method = data['selected_method']
    
    # Ask for amount
    await state.update_data(proof_file_id=message.photo[-1].file_id)
    curr_name = "INR" if method == "inr" else "Stars"
    await message.answer(f"🔢 <b>Enter Amount Paid ({curr_name})</b>\n\nJust type the number (e.g. 50).")
    await state.set_state(BuyVotes.waiting_for_amount)

@router.message(BuyVotes.waiting_for_amount)
async def receive_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except:
        await message.answer("❌ Invalid number. Try again.")
        return
    
    data = await state.get_data()
    ga = data['ga_doc']
    method = data['selected_method']
    
    # Calculate Votes
    rate = ga['rates'][method]
    votes_to_add = amount * rate
    
    # Create Transaction
    txn_id = generate_id(6)
    txn = {
        "txn_id": txn_id,
        "ga_id": ga['ga_id'],
        "user_id": message.from_user.id,
        "amount": amount,
        "method": method,
        "votes_to_add": votes_to_add,
        "proof": data['proof_file_id'],
        "status": "pending",
        "timestamp": datetime.now()
    }
    await transactions_col.insert_one(txn)
    
    # Send to Creator
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Approve", callback_data=f"appr_yes_{txn_id}")
    kb.button(text="❌ Reject", callback_data=f"appr_no_{txn_id}")
    kb.adjust(2)
    
    caption = (
        f"💰 <b>New Paid Vote Request</b>\n"
        f"User: {message.from_user.mention_html()}\n"
        f"Method: {method.upper()}\n"
        f"Amount: {amount}\n"
        f"Votes: {votes_to_add}\n"
        f"Proof attached above."
    )
    
    try:
        await bot.send_photo(chat_id=ga['creator_id'], photo=data['proof_file_id'], caption=caption, reply_markup=kb.as_markup())
        await message.answer("✅ <b>Proof Sent!</b> Waiting for admin approval.")
    except Exception as e:
        await message.answer("❌ Failed to contact admin. Try again later.")
        
    await state.clear()

# ---------------- ADMIN APPROVAL ---------------- #

@router.callback_query(F.data.startswith("appr_"))
async def handle_approval(call: CallbackQuery):
    action, txn_id = call.data.split("_")[1], call.data.split("_")[2]
    txn = await transactions_col.find_one({"txn_id": txn_id})
    
    if not txn or txn['status'] != 'pending':
        await call.answer("❌ Already processed.", show_alert=True)
        return

    if action == "no":
        await transactions_col.update_one({"txn_id": txn_id}, {"$set": {"status": "rejected"}})
        await call.message.edit_caption(caption=call.message.caption + "\n\n❌ <b>REJECTED</b>")
        await bot.send_message(txn['user_id'], "❌ Your paid vote request was rejected.")
    else:
        # Update DB
        await transactions_col.update_one({"txn_id": txn_id}, {"$set": {"status": "approved"}})
        
        # Add Votes
        await participants_col.update_one(
            {"ga_id": txn['ga_id'], "user_id": txn['user_id']},
            {"$inc": {"vote_count": txn['votes_to_add'], "paid_votes_count": txn['votes_to_add']}}
        )
        
        # Update Channel Post
        p = await participants_col.find_one({"ga_id": txn['ga_id'], "user_id": txn['user_id']})
        ga = await giveaways_col.find_one({"ga_id": txn['ga_id']})
        
        new_count = p['vote_count']
        
        kb = InlineKeyboardBuilder()
        kb.button(text=f"🗳 Vote ({new_count})", callback_data=f"vote_{p['user_id']}_{ga['ga_id']}")
        kb.adjust(1)
        
        try:
            await bot.edit_message_reply_markup(
                chat_id=p['channel_id'],
                message_id=p['msg_id'],
                reply_markup=kb.as_markup()
            )
        except:
            pass
            
        # Notifications
        await call.message.edit_caption(caption=call.message.caption + "\n\n✅ <b>APPROVED</b>")
        
        succ_msg = (
            f"✅ <b>Payment Approved!</b>\n"
            f"+{txn['votes_to_add']} Votes added.\n"
            f"Current Total: {new_count}"
        )
        await bot.send_message(txn['user_id'], succ_msg)
        
        try:
            await bot.send_message(ga['target_channel_id'], f"🚀 <b>PAID VOTES!</b>\n<blockquote>🧘‍♂️USER :{p['name']}</blockquote>\n<blockquote>💳 Purchased : {txn['votes_to_add']} votes!</blockquote>")
        except:
            pass
#---------- Stats
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    # Security Check
    if message.from_user.id not in OWNER_IDS:
        return

    # 1. Gather Data
    total_users = await users_col.count_documents({})
    total_gas = await giveaways_col.count_documents({})
    active_gas = await giveaways_col.count_documents({"status": "active"})

    text = (
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 <b>Total Users:</b> {total_users}\n"
        f"🎁 <b>Total Giveaways:</b> {total_gas}\n"
        f"🟢 <b>Active Giveaways:</b> {active_gas}"
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="🏆 Top Creators", callback_data="admin_top_users")
    kb.adjust(1)

    await message.answer(text, reply_markup=kb.as_markup())

@router.callback_query(F.data == "admin_top_users")
async def show_top_creators(call: CallbackQuery):
    if call.from_user.id not in OWNER_IDS:
        return

    # Aggregation to find users with most giveaways
    pipeline = [
        {"$group": {"_id": "$creator_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    
    results = await giveaways_col.aggregate(pipeline).to_list(None)
    
    text = "🏆 <b>Top Giveaway Creators</b>\n\n"
    
    if not results:
        text += "No data found."
    else:
        for idx, item in enumerate(results, 1):
            user_id = item['_id']
            count = item['count']
            
            # Try to get name from users collection
            user_doc = await users_col.find_one({"user_id": user_id})
            name = user_doc['first_name'] if user_doc else "Unknown"
            
            text += f"{idx}. {name} (<code>{user_id}</code>) - <b>{count} GAs</b>\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Back", callback_data="delete_msg") # Simple close/back
    
    # Check if message text is different to avoid errors
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await call.answer()

@router.callback_query(F.data == "delete_msg")
async def delete_msg(call: CallbackQuery):
    await call.message.delete()


#-----------------Set Join 
@router.message(Command("setjoin"))
async def cmd_setjoin(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return

    # Fetch current settings
    settings = await settings_col.find_one({"type": "force_join"})
    current_channels = settings.get('channels', []) if settings else []

    text = (
        f"🛡 <b>Force Join Settings</b>\n"
        f"Current Channels: {len(current_channels)}/10\n\n"
    )
    
    for i, ch in enumerate(current_channels, 1):
        text += f"{i}. ID: <code>{ch['id']}</code>\n"

    kb = InlineKeyboardBuilder()
    if len(current_channels) < 10:
        kb.button(text="➕ Add Channel", callback_data="add_fsub")
    kb.button(text="🗑 Clear All", callback_data="clear_fsub")
    kb.button(text="❌ Close", callback_data="delete_msg")
    kb.adjust(1)

    await message.answer(text, reply_markup=kb.as_markup())

@router.callback_query(F.data == "clear_fsub")
async def clear_fsub(call: CallbackQuery):
    if call.from_user.id not in OWNER_IDS: return
    await settings_col.update_one(
        {"type": "force_join"}, 
        {"$set": {"channels": []}}, 
        upsert=True
    )
    await call.answer("All channels removed!", show_alert=True)
    await call.message.delete()

@router.callback_query(F.data == "add_fsub")
async def start_add_fsub(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in OWNER_IDS: return
    
    await call.message.answer(
        "📝 <b>Add Must-Join Channel</b>\n\n"
        "Send the <b>Channel ID</b> and <b>Invite Link</b> separated by a space.\n"
        "Example:\n<code>-1001234567890 https://t.me/+AbCdEfG</code>\n\n"
        "⚠️ <i>Make sure the Bot is Admin in that channel!</i>"
    )
    await state.set_state(SetJoin.waiting_for_input)
    await call.answer()

@router.message(SetJoin.waiting_for_input)
async def process_fsub_input(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            await message.answer("❌ Invalid format. Use: <code>ID LINK</code>")
            return
        
        ch_id = int(parts[0])
        link = parts[1]

        # Verify bot admin status
        try:
            member = await bot.get_chat_member(chat_id=ch_id, user_id=bot.id)
            if member.status != ChatMemberStatus.ADMINISTRATOR:
                await message.answer("⚠️ I am not an admin in that channel! Promote me first.")
                return
        except Exception as e:
            await message.answer(f"❌ Could not access channel: {e}")
            return

        # Save to DB
        await settings_col.update_one(
            {"type": "force_join"},
            {"$push": {"channels": {"id": ch_id, "link": link}}},
            upsert=True
        )

        await message.answer("✅ <b>Channel Added to Force Subscription!</b>")
        await state.clear()
        
    except ValueError:
        
        await message.answer("❌ ID must be a number.")

#---------- SUPPORT 
@router.message(Command("support"))
async def cmd_support(message: Message):
    text = "<b>ADMIN</b> - @PirateCodez\n<b>SUPPORT-</b> @AXPNET\n<b>BOT-</b> @Axpvotebot\n\nDm admin for any issues or visit support"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="Admin", url="https://t.me/piratecodez")
    kb.adjust(1)
    kb.button(text="Support", url="https://t.me/axpnet")
    kb.adjust(1,1)
    await message.answer(text, reply_markup=kb.as_markup())
    
        
# ---------------- MANAGEMENT & LEADERBOARD ---------------- #

@router.callback_query(F.data.startswith("leaderboard_"))
async def show_leaderboard(call: CallbackQuery):
    ga_id = call.data.split("_")[1]
    
    pipeline = [
        {"$match": {"ga_id": ga_id}},
        {"$sort": {"vote_count": -1}},
        {"$limit": 10}
    ]
    cursor = participants_col.aggregate(pipeline)
    
    text = f"🏆 <b>LEADERBOARD (Top 10)</b>\n\n"
    i = 1
    async for p in cursor:
        paid_info = f" (Paid: {p.get('paid_votes_count', 0)})" if p.get('paid_votes_count', 0) > 0 else ""
        
        # --- FIX: ESCAPE THE NAME ---
        safe_name = html.quote(p['name']) 
        
        text += f"{i}. {safe_name} - <b>{p['vote_count']}</b>{paid_info}\n"
        i += 1
        
    if i == 1: text += "No participants yet."
    
    try:
        await call.message.edit_caption(
            caption=text, 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data=f"my_ga")]])
        )
    except Exception as e:
        # Fallback if text is too long or other edit errors occur
        await call.answer("Could not load leaderboard.", show_alert=True)
        logging.error(f"Leaderboard Error: {e}")
        

#-------
# --- MY GIVEAWAYS DASHBOARD ---
@router.callback_query(F.data == "my_ga")
async def my_ga_dashboard(call: CallbackQuery):
    # Main Menu for Giveaways
    kb = InlineKeyboardBuilder()
    # Created Categories
    kb.button(text="✍️ Created (Active)", callback_data="my_cr_active_0", style="primary")
    kb.button(text="📜 Created (Past)", callback_data="my_cr_past_0", style="primary")
    kb.adjust(2,2)
    kb.button(text="🤝 Joined (Active)", callback_data="my_jn_active_0", style="primary")
    kb.button(text="📂 Joined (Past)", callback_data="my_jn_past_0", style="primary")
    kb.adjust(2,2)
    kb.button(text="🔙 Back", callback_data="back_to_start", style="danger")
    kb.adjust(2,2,1)
    
    await call.message.edit_caption(
        caption="🎁 <b>My Giveaways</b>\nSelect a category:", 
        reply_markup=kb.as_markup()
    )

# --- CREATED LIST (Active vs Past) ---
@router.callback_query(F.data.startswith("my_cr_"))
async def list_created_gas(call: CallbackQuery):
    parts = call.data.split("_")
    # Format: my_cr_active_0 OR my_cr_past_0
    mode = parts[2] # 'active' or 'past'
    page = int(parts[3])
    
    user_id = call.from_user.id
    limit = 5
    skip = page * limit

    # Query Builder
    base_query = {"creator_id": user_id}
    if mode == "active":
        base_query["status"] = "active"
        title_text = "🟢 Active Created Giveaways"
    else:
        base_query["status"] = {"$ne": "active"} # Not active (ended/stopped)
        title_text = "🔴 Past Created Giveaways"

    total = await giveaways_col.count_documents(base_query)
    cursor = giveaways_col.find(base_query).sort("_id", -1).skip(skip).limit(limit)
    gas = await cursor.to_list(length=limit)

    kb = InlineKeyboardBuilder()
    
    if not gas:
        kb.button(text="🔙 Back", callback_data="my_ga", style="danger")
        await call.message.edit_caption(caption=f"❌ <b>No {mode} giveaways found.</b>", reply_markup=kb.as_markup())
        return

    for ga in gas:
        desc = ga.get('description', 'Giveaway')[:20]
        kb.button(text=f"{desc}..", callback_data=f"manage_ga_{ga['ga_id']}")
    kb.adjust(1)

    # Navigation
    navs = []
    if page > 0: navs.append(InlineKeyboardButton(text="⬅️", callback_data=f"my_cr_{mode}_{page-1}"))
    if total > (skip + limit): navs.append(InlineKeyboardButton(text="➡️", callback_data=f"my_cr_{mode}_{page+1}"))
    if navs: kb.row(*navs)
    
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="my_ga"))

    await call.message.edit_caption(caption=f"✍️ <b>{title_text}</b> (Pg {page+1})", reply_markup=kb.as_markup())

# --- JOINED LIST (Active vs Past) ---
@router.callback_query(F.data.startswith("my_jn_"))
async def list_joined_gas(call: CallbackQuery):
    parts = call.data.split("_")
    mode = parts[2] # 'active' or 'past'
    page = int(parts[3])
    
    user_id = call.from_user.id
    limit = 5
    skip = page * limit

    # 1. Get List of GA IDs user participated in
    p_cursor = participants_col.find({"user_id": user_id}).sort("_id", -1)
    user_participations = await p_cursor.to_list(None) # Get all first
    
    # 2. Filter by Status manually (since status is in giveaways_col, not participants)
    filtered_ga_ids = []
    
    for p in user_participations:
        ga = await giveaways_col.find_one({"ga_id": p['ga_id']})
        if not ga: continue
        
        if mode == "active" and ga['status'] == 'active':
            filtered_ga_ids.append(ga)
        elif mode == "past" and ga['status'] != 'active':
            filtered_ga_ids.append(ga)

    # 3. Apply Pagination to list
    total = len(filtered_ga_ids)
    paged_gas = filtered_ga_ids[skip : skip+limit]
    
    kb = InlineKeyboardBuilder()
    title_text = "🟢 Active Joined" if mode == "active" else "🔴 Past Joined"

    if not paged_gas:
        kb.button(text="🔙 Back", callback_data="my_ga", style="danger")
        await call.message.edit_caption(caption=f"❌ <b>No {mode} joined giveaways.</b>", reply_markup=kb.as_markup())
        return

    for ga in paged_gas:
        desc = ga.get('description', 'Giveaway')[:20]
        kb.button(text=f"{desc}..", callback_data=f"view_joined_{ga['ga_id']}")
    kb.adjust(1)

    # Navigation
    navs = []
    if page > 0: navs.append(InlineKeyboardButton(text="⬅️", callback_data=f"my_jn_{mode}_{page-1}"))
    if total > (skip + limit): navs.append(InlineKeyboardButton(text="➡️", callback_data=f"my_jn_{mode}_{page+1}"))
    if navs: kb.row(*navs)
    
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="my_ga", style="danger"))
    
    await call.message.edit_caption(caption=f"🤝 <b>{title_text}</b> (Pg {page+1})", reply_markup=kb.as_markup())

# --- JOINED DETAILS VIEW (With Buttons) ---
@router.callback_query(F.data.startswith("view_joined_"))
async def view_joined_details(call: CallbackQuery):
    ga_id = call.data.split("_")[2]
    user_id = call.from_user.id
    
    ga = await giveaways_col.find_one({"ga_id": ga_id})
    p = await participants_col.find_one({"ga_id": ga_id, "user_id": user_id})
    
    if not ga or not p:
        await call.answer("Data unavailable.", show_alert=True)
        return

    status_icon = "🟢 Active" if ga['status'] == 'active' else "🔴 Ended"
    
    text = (
        f"🎁 <b>Giveaway Details</b>\n"
        f"📝 <b>Desc:</b> {ga.get('description')}\n\n"
        f"📊 <b>Status:</b> {status_icon}\n"
        f"🗳 <b>Vote Count:</b> {p['vote_count']}\n"
        f"👤 <b>Your Name:</b> {p['name']}\n"
    )
    
    kb = InlineKeyboardBuilder()
    
    # 1. Buy Votes
    if ga['status'] == 'active' and ga.get('paid_enabled'):
        kb.button(text="💰 Buy Votes", callback_data=f"buy_start_{ga_id}", style="primary")
    
    # 2. View Leaderboard (Requested Feature)
    kb.button(text="🏆 Leaderboard", callback_data=f"leaderboard_{ga_id}", style="primary")
    kb.adjust(1)
    
    # 3. Get Links (New Button)
    kb.button(text="🔗 Get Channel & Post Link", callback_data=f"get_links_{ga_id}", style="primary")
    
    kb.button(text="🔙 Back", callback_data="my_ga", style="danger")
    
    await call.message.edit_caption(caption=text, reply_markup=kb.as_markup())
    

@router.callback_query(F.data.startswith("manage_ga_"))
async def manage_ga_menu(call: CallbackQuery):
    ga_id = call.data.split("_")[2]
    ga = await giveaways_col.find_one({"ga_id": ga_id})
    
    text = (
        f"⚙️ <b>Management Panel</b>\n"
        f"ID: <code>{ga_id}</code>\n"
        f"Status: {ga['status']}\n"
        f"Participants: {ga.get('participants_count', 0)}\n"
        f"Link: https://t.me/{BOTUSER}?start={ga_id}"
    )
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🏆 Leaderboard", callback_data=f"leaderboard_{ga_id}", style="primary")
    
    if ga['status'] == 'active':
        kb.button(text="🛑 Stop Paid Votes", callback_data=f"act_stoppaid_{ga_id}", style="danger")
        kb.button(text="🛑 Stop Participation", callback_data=f"act_stoppart_{ga_id}", style="danger")
        kb.button(text="🔚 End Giveaway", callback_data=f"act_end_{ga_id}", style="danger")
    
    kb.button(text="🗑 Clear Channel Posts", callback_data=f"act_clear_{ga_id}", style="danger")
    kb.button(text="🔙 Back", callback_data="my_ga", style="primary")
    kb.adjust(1)
    
    # --- IMAGE LOGIC ADDED HERE ---
    try:
        # Try to edit the existing photo to the WELCOME_IMAGE
        media = InputMediaPhoto(media=WELCOME_IMAGE, caption=text)
        await call.message.edit_media(media=media, reply_markup=kb.as_markup())
    except Exception:
        # If the previous message was text-only (or edit fails), delete and send new photo
        await call.message.delete()
        await call.message.answer_photo(
            photo=WELCOME_IMAGE,
            has_spoiler=True,
            caption=text,
            reply_markup=kb.as_markup()
        )
        

@router.callback_query(F.data.startswith("act_"))
async def handle_actions(call: CallbackQuery):
    action, ga_id = call.data.split("_")[1], call.data.split("_")[2]
    
    if action == "end":
        await end_giveaway_logic(ga_id)
        await call.answer("Giveaway Ended!", show_alert=True)
    
    elif action == "stoppaid":
        await giveaways_col.update_one({"ga_id": ga_id}, {"$set": {"paid_enabled": False}})
        await call.answer("Paid votes disabled.", show_alert=True)
        
    elif action == "stoppart":
        await giveaways_col.update_one({"ga_id": ga_id}, {"$set": {"status": "participation_stopped"}})
        await call.answer("Participation stopped.", show_alert=True)
        
    elif action == "clear":
        await call.answer("Deleting posts... this may take time.")
        parts = participants_col.find({"ga_id": ga_id})
        count = 0
        async for p in parts:
            try:
                await bot.delete_message(chat_id=p['channel_id'], message_id=p['msg_id'])
                count += 1
                await asyncio.sleep(0.05)
            except:
                pass
        await call.message.answer(f"🗑 <b>Cleared {count} posts from channel.</b>")
    
    await manage_ga_menu(call)

# ---------------- BOT ADDED HANDLER ---------------- #

# ---------------- CONFIGURATION COMMAND ---------------- #

@router.message(Command("setposttext"))
async def set_post_text_command(message: Message):
    # 1. Security Check: Only Owners can use this
    if message.from_user.id not in OWNER_IDS:
        return

    # 2. Extract HTML content (Preserves formatting like Bold, Links, Quotes)
    full_html = message.html_text
    
    # Split to separate the command "/setposttext" from the actual text
    parts = full_html.split(maxsplit=1)
    
    if len(parts) < 2:
        await message.answer(
            "⚠️ <b>Usage:</b>\n"
            "<code>/setposttext [Your Custom Message]</code>\n\n"
            "<b>Available Variables:</b>\n"
            "<code>{channel}</code> - Shows Channel Name\n"
            "<code>{user}</code> - Shows User Name\n"
            "<code>{link}</code> - Shows Channel Link (if public)\n\n"
            "<i>You can use HTML tags, Blockquotes, Bold, etc.</i>"
        )
        return

    custom_text = parts[1] # This is the text with formatting

    # 3. Save to Database
    await settings_col.update_one(
        {"_id": "on_admin_add_text"},
        {"$set": {"text": custom_text}},
        upsert=True
    )

    await message.answer(
        "✅ <b>Welcome Message Updated!</b>\n\n"
        "<b>Preview of saved text:</b>\n"
        f"{custom_text}",
        disable_web_page_preview=True
    )
    
# ---------------- BOT ADDED HANDLER (UPDATED) ---------------- #

@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=ADMINISTRATOR))
async def on_bot_added_as_admin(event: ChatMemberUpdated):
    """
    Triggered when Bot is promoted to Admin.
    1. Saves channel to DB.
    2. Sends CUSTOM message to user.
    """
    new_chat = event.chat
    user = event.from_user
    
    # 1. Store/Update in Database
    await channels_col.update_one(
        {"chat_id": new_chat.id},
        {"$set": {
            "chat_id": new_chat.id,
            "title": new_chat.title,
            "username": new_chat.username,
            "type": new_chat.type,
            "added_by": user.id,
            "updated_at": datetime.now()
        }},
        upsert=True
    )

    # 2. Fetch Custom Text from DB
    setting = await settings_col.find_one({"_id": "on_admin_add_text"})
    
    # Default fallback if you haven't set text yet
    if setting and setting.get('text'):
        text_template = setting.get('text')
    else:
        text_template = (
            "🎉 <b>Thanks for adding me!</b>\n\n"
            "I am now an Admin in: <b>{channel}</b>\n\n"
            "You can now use /createpost to publish messages."
        )

    # 3. Prepare Variables
    channel_name = html.quote(new_chat.title)
    user_name = html.quote(user.full_name)
    chat_link = f"https://t.me/{new_chat.username}" if new_chat.username else "No Public Link"

    # 4. Replace Variables in the Template
    # We use .replace() instead of .format() to avoid crashing on random curly braces in user text
    final_text = text_template.replace("{channel}", channel_name)\
                              .replace("{user}", user_name)\
                              .replace("{link}", chat_link)

    # 5. Send DM to User
    try:
        kb = InlineKeyboardBuilder()
        if new_chat.username:
            kb.button(text="↗️ Go to Channel", url=chat_link)
        
        await bot.send_message(
            chat_id=user.id,
            text=final_text,
            reply_markup=kb.as_markup(),
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.warning(f"Could not DM user {user.id}: {e}")
        

# ------------------------------------------------------- #

# ------------------------------------------------------- #
#                  CREATE POST TOOL (REWRITTEN)           #
# ------------------------------------------------------- #

# Unified Entry Point: Command AND Callback
@router.message(Command("createpost"))
@router.callback_query(F.data == "create_post_start")
async def post_start_unified(event: Union[Message, CallbackQuery], state: FSMContext):
    
    # Check Admin/Membership if needed (Optional)
    # if event.from_user.id not in OWNER_IDS: return 

    await state.clear()
    await state.set_state(PostMaker.waiting_for_media)

    caption_text = (
        "📸 <b>Create New Post: Step 1</b>\n\n"
        "Send a <b>Photo</b> to include in your post.\n"
        "Or click <b>Skip Media</b> for a text-only post."
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Skip Media", callback_data="post_skip_media", style="primary")
    kb.button(text="🔙 Cancel", callback_data="back_to_start", style="danger")

    # Handle Message (Command) vs Callback (Button)
    if isinstance(event, Message):
        await event.answer(caption_text, reply_markup=kb.as_markup())
    else:
        # Try to edit, fall back to send if types mismatch (e.g. editing text to photo)
        try:
            await event.message.edit_caption(caption=caption_text, reply_markup=kb.as_markup())
        except:
            await event.message.delete()
            await event.message.answer(caption_text, reply_markup=kb.as_markup())

@router.callback_query(F.data == "post_skip_media", PostMaker.waiting_for_media)
async def post_skip_media(call: CallbackQuery, state: FSMContext):
    await state.update_data(media_file_id=None, media_type="text")
    await call.message.delete() 
    await call.message.answer("📝 <b>Step 2: Send Caption</b>\n\nSend the text for your post.\nFormatting (Bold, Italic, HTML) will be preserved.")
    await state.set_state(PostMaker.waiting_for_caption)

@router.message(PostMaker.waiting_for_media)
async def post_receive_media(message: Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        await state.update_data(media_file_id=file_id, media_type="photo")
        await message.answer("📝 <b>Step 2: Send Caption</b>\n\nSend the text for your post.\nFormatting will be preserved.")
        await state.set_state(PostMaker.waiting_for_caption)
    else:
        await message.answer("❌ Please send a Photo or click Skip.")

@router.message(PostMaker.waiting_for_caption)
async def post_receive_caption(message: Message, state: FSMContext):
    caption_text = message.html_text # Preserves formatting
    await state.update_data(caption=caption_text)
    
    await message.answer(
        "🔘 <b>Step 3: Add Buttons</b>\n\n"
        "Send buttons in this format:\n"
        "<code>Name - Link</code>\n\n"
        "<b>For multiple buttons in one row, use && :</b>\n"
        "<code>Btn1 - Link1 && Btn2 - Link2</code>\n"
        "<code>Btn3 - Link3</code>\n\n"
        "Type /skip to send without buttons.",
        disable_web_page_preview=True
    )
    await state.set_state(PostMaker.waiting_for_buttons)

@router.message(PostMaker.waiting_for_buttons)
async def post_receive_buttons(message: Message, state: FSMContext):
    text = message.text.strip()
    kb = InlineKeyboardBuilder()
    
    if text != "/skip":
        rows = text.split('\n')
        row_widths = []
        try:
            for row in rows:
                if not row.strip(): continue
                btns = row.split('&&')
                row_widths.append(len(btns))
                for btn in btns:
                    if '-' in btn:
                        label, url = btn.split('-', 1)
                        kb.button(text=label.strip(), url=url.strip())
                    else:
                        await message.answer(f"❌ Invalid format: {btn}")
                        return
            kb.adjust(*row_widths)
        except Exception as e:
            await message.answer(f"❌ Error parsing buttons: {e}")
            return

    markup = kb.as_markup()
    await state.update_data(reply_markup=markup)
    
    # --- PREVIEW ---
    data = await state.get_data()
    
    control_kb = InlineKeyboardBuilder()
    control_kb.button(text="🚀 Send to Channel", callback_data="post_select_channel")
    control_kb.button(text="🗑 Discard", callback_data="back_to_start")
    control_kb.adjust(1)
    
    await message.answer("👀 <b>Preview:</b>")
    try:
        if data['media_type'] == 'photo':
            await message.answer_photo(photo=data['media_file_id'], caption=data['caption'], reply_markup=markup)
        else:
            await message.answer(text=data['caption'], reply_markup=markup, disable_web_page_preview=True)
            
        await message.answer("👆 <b>This is your preview.</b>\nReady to publish?", reply_markup=control_kb.as_markup())
    except Exception as e:
        await message.answer(f"❌ Error generating preview: {e}")

# ---------------- CHANNEL SELECTION LOGIC ---------------- #

@router.callback_query(F.data == "post_select_channel")
async def post_choose_channel_start(call: CallbackQuery, state: FSMContext):
    await show_channel_selection(call, page=0)

@router.callback_query(F.data.startswith("post_page_"))
async def post_choose_channel_page(call: CallbackQuery, state: FSMContext):
    page = int(call.data.split("_")[2])
    await show_channel_selection(call, page=page)

async def show_channel_selection(call: CallbackQuery, page: int):
    user_id = call.from_user.id
    ITEMS_PER_PAGE = 10
    
    # 1. Fetch potential channels from TWO sources:
    # Source A: New 'channels_col' (Where bot was added via new event)
    # Source B: 'giveaways_col' (History of where user created giveaways)
    
    unique_chats = {} # {chat_id: title}

    # -- Source A: Direct Admin DB --
    async for ch in channels_col.find({"added_by": user_id}):
        unique_chats[ch['chat_id']] = ch['title']

    # -- Source B: Giveaway History --
    async for ga in giveaways_col.find({"creator_id": user_id}):
        c_id = ga.get('target_channel_id')
        if c_id and c_id not in unique_chats:
            unique_chats[c_id] = ga.get('target_channel_title', str(c_id))

    # 2. Validation Loop (Live API Check)
    valid_chats = []
    
    # Sort for consistency
    all_ids = sorted(unique_chats.keys())

    for ch_id in all_ids:
        try:
            # Check Bot Permission
            bot_member = await bot.get_chat_member(ch_id, bot.id)
            if bot_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                continue
            
            # Check User Permission (Must be admin to post via bot)
            user_member = await bot.get_chat_member(ch_id, user_id)
            if user_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                continue
            
            # Get Real Title if we only have ID
            title = unique_chats[ch_id]
            if str(title).startswith("-100") or str(title).isdigit():
                 try:
                     chat_info = await bot.get_chat(ch_id)
                     title = chat_info.title
                 except: pass

            valid_chats.append({'id': ch_id, 'title': title})
        except Exception:
            continue # Chat deleted or bot kicked

    # 3. Empty State Handling
    if not valid_chats:
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ How to add?", callback_data="noop") # Dummy button or link
        kb.button(text="🔙 Back", callback_data="back_to_start")
        await call.message.edit_text(
            "❌ <b>No Accessible Channels Found.</b>\n\n"
            "1. Add this bot to your Channel.\n"
            "2. Promote it to <b>Admin</b>.\n"
            "3. Try again.\n\n"
            "<i>Only channels where BOTH you and the bot are admins will appear here.</i>",
            reply_markup=kb.as_markup()
        )
        return

    # 4. Pagination
    total_items = len(valid_chats)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_batch = valid_chats[start_idx:end_idx]
    
    # 5. Build List
    kb = InlineKeyboardBuilder()
    for chat in current_batch:
        kb.button(text=f"📢 {chat['title']}", callback_data=f"publish_{chat['id']}")
    kb.adjust(1)
    
    # Navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"post_page_{page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"post_page_{page+1}"))
    
    kb.row(*nav_buttons)
    kb.row(InlineKeyboardButton(text="🔙 Cancel", callback_data="back_to_start"))

    msg_text = "📤 <b>Select Destination Channel:</b>"
    
    # Safe Edit
    try:
        await call.message.edit_text(msg_text, reply_markup=kb.as_markup())
    except:
        await call.message.delete()
        await call.message.answer(msg_text, reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("publish_"))
async def post_publish(call: CallbackQuery, state: FSMContext):
    try:
        target_id = int(call.data.split("_")[1])
        data = await state.get_data()
        
        if data['media_type'] == 'photo':
            await bot.send_photo(
                chat_id=target_id,
                photo=data['media_file_id'],
                caption=data['caption'],
                reply_markup=data.get('reply_markup')
            )
        else:
            await bot.send_message(
                chat_id=target_id,
                text=data['caption'],
                reply_markup=data.get('reply_markup'),
                disable_web_page_preview=True
            )
            
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Posted! Send Another?", callback_data="create_post_start")
        kb.button(text="🏠 Home", callback_data="back_to_start")
        kb.adjust(1)
        
        await call.message.delete()
        await call.message.answer("✅ <b>Post published successfully!</b>", reply_markup=kb.as_markup())
        await state.clear()
        
    except Exception as e:
        await call.answer(f"❌ Failed: {str(e)}", show_alert=True)
        

# ---------------- BROADCAST COMMAND ---------------- #

@router.message(Command("broadcast"))
async def broadcast_command(message: Message):
    # 1. Security Check
    if message.from_user.id not in OWNER_IDS:
        return
    
    # 2. Input Validation
    if not message.reply_to_message:
        await message.answer("⚠️ <b>Error:</b> Please reply to the message you want to broadcast.")
        return

    source_msg = message.reply_to_message
    
    # 3. Status Message
    status_msg = await message.answer("🚀 <b>Broadcast started...</b>\n<i>Do not delete the original message.</i>")
    
    success = 0
    blocked = 0
    total = 0
    
    # 4. Fetch Users
    users = users_col.find({})
    
    async for user in users:
        total += 1
        user_id = user.get('user_id')
        
        if not user_id: continue

        try:
            # COPY_MESSAGE: The only way to preserve exact formatting, media, and buttons
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source_msg.chat.id,
                message_id=source_msg.message_id,
                reply_markup=source_msg.reply_markup # Preserves buttons if the source has them
            )
            success += 1
            await asyncio.sleep(0.05) # Safe rate limit (approx 20 msgs/sec)
            
        except Exception as e:
            # If user blocked bot or account deleted
            blocked += 1
            # Optional: Remove invalid user from DB to clean up
            # await users_col.delete_one({"user_id": user_id})

        # Update status every 200 users (to avoid flood limits on the status msg itself)
        if total % 200 == 0:
            try:
                await status_msg.edit_text(
                    f"🚀 <b>Broadcasting...</b>\n"
                    f"✅ Sent: {success}\n"
                    f"❌ Failed: {blocked}\n"
                    f"📊 Total Checked: {total}"
                )
            except: pass

    # 5. Final Report
    await status_msg.edit_text(
        f"✅ <b>Broadcast Completed!</b>\n\n"
        f"👥 Total Users: {total}\n"
        f"✅ Success: {success}\n"
        f"❌ Failed/Blocked: {blocked}"
    )
    
# ---------------- MAIN ---------------- #
async def main():
    # --- SCHEDULER SETUP ---
    # Add the Global Resync job: Runs every 2 minutes
    scheduler.add_job(run_global_resync, 'interval', minutes=1)
    
    # Existing cleanup job
    scheduler.add_job(clean_expired_global_channels, 'interval', minutes=30)
    
    scheduler.start() # Start Scheduler
    
    await bot.delete_webhook(drop_pending_updates=True)
    print("Bot is running with Auto-Resync (2 min)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
