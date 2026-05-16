import os
import re
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import yt_dlp
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot token from environment variable
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN found in environment variables!")

# Temporary download directory
DOWNLOAD_DIR = "downloads"
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

# Store active downloads to allow cancellation
active_downloads = {}
download_cancelled = {}


def is_tiktok_url(url: str) -> bool:
    """Check if URL is a TikTok link"""
    patterns = [
        r"https?://(vm\.|www\.)?tiktok\.com/(@[\w.-]+/video/\d+|t/[\w]+)",
        r"https?://(vm\.|www\.)?tiktok\.com/[@\w.-]+/video/\d+",
        r"https?://(vm\.|www\.)?tiktok\.com/[\w]+",
        r"https?://(vm\.|www\.)?tiktok\.com/@[\w.-]+",
    ]
    return any(re.match(pattern, url) for pattern in patterns)


def extract_username_from_url(url: str) -> str | None:
    """Extract username from TikTok URL"""
    match = re.search(r"tiktok\.com/@([\w.-]+)", url)
    if match:
        return match.group(1)
    return None


def clean_caption(caption: str, max_length: int = 1024) -> str:
    """Clean and truncate caption for Telegram (max 1024 chars)"""
    if not caption:
        return ""
    
    caption = ' '.join(caption.split())
    
    if len(caption) > max_length:
        caption = caption[:max_length - 3] + "..."
    
    return caption


def is_within_timeframe(upload_date: str, days: int) -> bool:
    """Check if video is within specified days"""
    if not upload_date:
        return True
    
    try:
        video_date = datetime.strptime(upload_date, "%Y%m%d")
        cutoff_date = datetime.now() - timedelta(days=days)
        return video_date >= cutoff_date
    except:
        return True


async def get_all_user_videos(username: str, user_id: int = None) -> list[dict]:
    """Get ALL video information from user"""
    all_videos = []
    
    user_url = f"https://www.tiktok.com/@{username}"
    
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": 999,
        "playlistreverse": False,
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
    }
    
    # Check if download was cancelled
    if user_id and download_cancelled.get(user_id, False):
        logger.info(f"Download cancelled for user {user_id}")
        return []
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Fetching videos for @{username}...")
            info = await asyncio.to_thread(ydl.extract_info, user_url, download=False)
            
            if 'entries' not in info:
                return []
            
            entries = list(info['entries'])
            logger.info(f"Found {len(entries)} total videos for @{username}")
            
            for entry in entries:
                # Check for cancellation
                if user_id and download_cancelled.get(user_id, False):
                    logger.info(f"Download cancelled for user {user_id} during fetch")
                    return []
                
                if entry:
                    video_data = {
                        'id': entry.get('id'),
                        'title': entry.get('title', 'Untitled'),
                        'upload_date': entry.get('upload_date'),
                        'view_count': entry.get('view_count', 0),
                        'like_count': entry.get('like_count', 0),
                        'comment_count': entry.get('comment_count', 0),
                        'duration': entry.get('duration', 0),
                        'url': f"https://www.tiktok.com/@{username}/video/{entry['id']}"
                    }
                    all_videos.append(video_data)
            
            return all_videos
            
    except Exception as e:
        logger.error(f"Get user videos error: {e}")
        return []


async def download_tiktok_video(url: str, user_id: int = None, video_num: int = None, total: int = None) -> tuple[str | None, str | None, str | None]:
    """Download single TikTok video with cancellation support"""
    
    # Check if download was cancelled
    if user_id and download_cancelled.get(user_id, False):
        logger.info(f"Download cancelled for user {user_id} before video {video_num}")
        return None, "Download cancelled by user", None
    
    ydl_opts = {
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "format": "best",
        "geo_bypass": True,
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            file_path = ydl.prepare_filename(info)
            
            if not os.path.exists(file_path) and info.get("ext"):
                file_path = f"{DOWNLOAD_DIR}/{info['id']}.{info['ext']}"
            
            # Extract caption/description
            caption = info.get('description') or info.get('title') or ""
            
            # Add video stats
            stats = []
            if info.get('view_count'):
                stats.append(f"👁️ {info['view_count']:,} views")
            if info.get('like_count'):
                stats.append(f"❤️ {info['like_count']:,} likes")
            if info.get('comment_count'):
                stats.append(f"💬 {info['comment_count']:,} comments")
            
            if info.get('upload_date'):
                try:
                    upload_date = datetime.strptime(info['upload_date'], "%Y%m%d")
                    stats.append(f"📅 {upload_date.strftime('%B %d, %Y')}")
                except:
                    pass
            
            if stats:
                caption = f"{caption}\n\n{' | '.join(stats)}" if caption else ' | '.join(stats)
            
            caption = f"{caption}\n\n🎥 Downloaded without watermark!" if caption else "🎥 Downloaded without watermark!"
            
            return file_path, None, caption
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None, str(e), None


async def download_videos_by_timeframe(username: str, days: int, limit: int = None, user_id: int = None, update_msg = None) -> list[tuple[str, str, str]]:
    """Download videos with cancellation support"""
    
    # Check for cancellation
    if user_id and download_cancelled.get(user_id, False):
        return []
    
    # Get ALL videos from the user
    all_videos = await get_all_user_videos(username, user_id)
    
    if not all_videos:
        return []
    
    # Filter videos
    if days < 999:
        filtered_videos = []
        for video in all_videos:
            if is_within_timeframe(video.get('upload_date'), days):
                filtered_videos.append(video)
    else:
        filtered_videos = all_videos
    
    if limit and limit > 0:
        filtered_videos = filtered_videos[:limit]
    
    if not filtered_videos:
        return []
    
    logger.info(f"Downloading {len(filtered_videos)} videos for @{username}")
    
    # Download filtered videos
    downloaded_files = []
    total = len(filtered_videos)
    
    for idx, video in enumerate(filtered_videos, 1):
        # Check for cancellation before each download
        if user_id and download_cancelled.get(user_id, False):
            logger.info(f"Download cancelled for user {user_id} at video {idx}/{total}")
            if update_msg:
                await update_msg.edit_text(
                    f"⏹️ *Download Cancelled!*\n\n"
                    f"Stopped at video {idx-1}/{total} from @{username}\n"
                    f"Downloaded {len(downloaded_files)} videos successfully.\n\n"
                    f"Use /download @{username} again to continue!",
                    parse_mode="Markdown"
                )
            return downloaded_files
        
        file_path, error, caption = await download_tiktok_video(video['url'], user_id, idx, total)
        
        if file_path and not error:
            date_str = ""
            if video.get('upload_date'):
                try:
                    upload_date = datetime.strptime(video['upload_date'], "%Y%m%d")
                    date_str = f" 📅 {upload_date.strftime('%b %d, %Y')}"
                except:
                    pass
            
            video_caption = f"📹 Video {idx}/{total}{date_str} from @{username}\n\n{caption}"
            downloaded_files.append((file_path, f"Video {idx}", video_caption))
            logger.info(f"Downloaded {idx}/{total} videos from @{username}")
            
            # Update progress message every 5 videos
            if update_msg and idx % 5 == 0:
                try:
                    await update_msg.edit_text(
                        f"📥 Downloading videos from @{username}...\n"
                        f"Progress: {idx}/{total} videos downloaded\n"
                        f"⏳ Please wait or send /cancel to stop"
                    )
                except:
                    pass
        
        await asyncio.sleep(1)
    
    return downloaded_files


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message"""
    welcome_text = (
        "🎬 *TikTok Video Downloader Bot*\n\n"
        "I can download TikTok videos without watermark!\n\n"
        "*What I can do:*\n"
        "📹 • Download single video from link\n"
        "👤 • Download ALL videos from a profile\n"
        "⏰ • Filter videos by date (7, 14, 30 days, etc.)\n"
        "⏹️ • Cancel ongoing downloads\n"
        "📝 • Includes original captions and stats\n"
        "🔥 • No watermark, high quality\n\n"
        "*How to use:*\n"
        "1️⃣ Send a TikTok video link → Download single video\n"
        "2️⃣ Send a profile link → Choose download option\n"
        "3️⃣ Use /download @username → Download videos\n"
        "4️⃣ Use /cancel → Stop ongoing download\n"
        "5️⃣ Use /stats @username → Get profile stats\n\n"
        "*Examples:*\n"
        "• https://vm.tiktok.com/xxxxxx/\n"
        "• https://www.tiktok.com/@username\n"
        "• /download @username"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel ongoing download"""
    user_id = update.effective_user.id
    
    if user_id in active_downloads:
        download_cancelled[user_id] = True
        await update.message.reply_text(
            "⏹️ *Cancelling download...*\n\n"
            "Your download will be stopped after the current video finishes.\n"
            "Please wait a moment.\n\n"
            "Use /download to start a new download!",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ No active download found!\n\n"
            "Use /download @username to start downloading videos.",
            parse_mode="Markdown"
        )


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /download command for usernames"""
    user_id = update.effective_user.id
    
    # Clear any previous cancellation flag
    if user_id in download_cancelled:
        del download_cancelled[user_id]
    
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a username!\n\n"
            "Usage:\n"
            "/download @username\n\n"
            "Then choose download option from the menu.\n\n"
            "Example: /download @therock\n\n"
            "To stop a download, use /cancel"
        )
        return
    
    username = context.args[0].lstrip('@')
    
    # Show time period selection
    keyboard = [
        [InlineKeyboardButton("🆕 Last 5 Videos", callback_data=f"time_{username}_5videos")],
        [InlineKeyboardButton("🆕 Last 10 Videos", callback_data=f"time_{username}_10videos")],
        [InlineKeyboardButton("🆕 Last 20 Videos", callback_data=f"time_{username}_20videos")],
        [InlineKeyboardButton("📅 Last 7 Days", callback_data=f"time_{username}_7days")],
        [InlineKeyboardButton("📅 Last 14 Days", callback_data=f"time_{username}_14days")],
        [InlineKeyboardButton("📅 Last 30 Days", callback_data=f"time_{username}_30days")],
        [InlineKeyboardButton("📅 Last 2 Months", callback_data=f"time_{username}_60days")],
        [InlineKeyboardButton("📅 Last 3 Months", callback_data=f"time_{username}_90days")],
        [InlineKeyboardButton("🎬 ALL Videos", callback_data=f"time_{username}_all")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📱 *@{username}*\n\n"
        f"Select download option:\n"
        f"⚠️ Send /cancel to stop download at any time",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stats command to get profile statistics"""
    if not context.args:
        await update.message.reply_text(
            "Usage: /stats @username\nExample: /stats @therock"
        )
        return
    
    username = context.args[0].lstrip('@')
    processing_msg = await update.message.reply_text(f"📊 Fetching stats for @{username}... (This may take a moment)")
    
    try:
        all_videos = await get_all_user_videos(username)
        
        if not all_videos:
            await processing_msg.edit_text(f"❌ Could not fetch stats for @{username}")
            return
        
        total_videos = len(all_videos)
        stats_text = f"📊 *@{username} Statistics*\n\n"
        stats_text += f"📹 Total Videos Found: {total_videos}\n"
        
        total_views = sum(v.get('view_count', 0) for v in all_videos)
        stats_text += f"👁️ Total Views: {total_views:,}\n"
        
        if total_videos > 0:
            avg_views = total_views // total_videos
            stats_text += f"📊 Avg Views/Video: {avg_views:,}\n"
        
        most_viewed = max(all_videos, key=lambda x: x.get('view_count', 0))
        if most_viewed and most_viewed.get('view_count', 0) > 0:
            stats_text += f"\n⭐ *Most Viewed Video*\n"
            stats_text += f"👁️ {most_viewed.get('view_count', 0):,} views\n"
        
        last_5 = len(all_videos[:5])
        last_10 = len(all_videos[:10])
        last_20 = len(all_videos[:20])
        last_7 = sum(1 for v in all_videos if is_within_timeframe(v.get('upload_date'), 7))
        last_14 = sum(1 for v in all_videos if is_within_timeframe(v.get('upload_date'), 14))
        last_30 = sum(1 for v in all_videos if is_within_timeframe(v.get('upload_date'), 30))
        last_60 = sum(1 for v in all_videos if is_within_timeframe(v.get('upload_date'), 60))
        last_90 = sum(1 for v in all_videos if is_within_timeframe(v.get('upload_date'), 90))
        
        stats_text += f"\n📅 *Recent Videos*\n"
        stats_text += f"🆕 Last 5 videos: {last_5}\n"
        stats_text += f"🆕 Last 10 videos: {last_10}\n"
        stats_text += f"🆕 Last 20 videos: {last_20}\n"
        stats_text += f"📅 Last 7 days: {last_7}\n"
        stats_text += f"📅 Last 14 days: {last_14}\n"
        stats_text += f"📅 Last 30 days: {last_30}\n"
        stats_text += f"📅 Last 2 months: {last_60}\n"
        stats_text += f"📅 Last 3 months: {last_90}\n"
        
        stats_text += f"\n📥 Use /download @{username} to download videos!\n"
        stats_text += f"💡 Tip: Send /cancel to stop downloading anytime!"
        
        await processing_msg.edit_text(stats_text, parse_mode="Markdown")
                
    except Exception as e:
        logger.error(f"Stats error: {e}")
        await processing_msg.edit_text(f"❌ Error fetching stats: {str(e)[:100]}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user messages containing TikTok URLs"""
    message_text = update.message.text.strip()
    
    if not is_tiktok_url(message_text):
        await update.message.reply_text(
            "❌ Please send a valid TikTok link!\n\n"
            "Examples:\n"
            "• Video: https://vm.tiktok.com/xxxxxx/\n"
            "• Profile: https://www.tiktok.com/@username\n"
            "• Command: /download @username\n"
            "• Command: /cancel to stop download"
        )
        return
    
    username = extract_username_from_url(message_text)
    is_profile = "tiktok.com/@" in message_text and "/video/" not in message_text
    
    if is_profile and username:
        keyboard = [
            [InlineKeyboardButton("🆕 Last 5 Videos", callback_data=f"time_{username}_5videos")],
            [InlineKeyboardButton("🆕 Last 10 Videos", callback_data=f"time_{username}_10videos")],
            [InlineKeyboardButton("🆕 Last 20 Videos", callback_data=f"time_{username}_20videos")],
            [InlineKeyboardButton("📅 Last 7 Days", callback_data=f"time_{username}_7days")],
            [InlineKeyboardButton("📅 Last 14 Days", callback_data=f"time_{username}_14days")],
            [InlineKeyboardButton("📅 Last 30 Days", callback_data=f"time_{username}_30days")],
            [InlineKeyboardButton("📅 Last 2 Months", callback_data=f"time_{username}_60days")],
            [InlineKeyboardButton("📅 Last 3 Months", callback_data=f"time_{username}_90days")],
            [InlineKeyboardButton("🎬 ALL Videos", callback_data=f"time_{username}_all")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📱 Fetching video information for @{username}... ⏳",
            parse_mode="Markdown"
        )
        
        all_videos = await get_all_user_videos(username)
        video_count = len(all_videos)
        
        await update.message.reply_text(
            f"📱 *Profile: @{username}*\n"
            f"📹 Total videos found: {video_count}\n\n"
            f"Select download option:\n"
            f"⚠️ Send /cancel to stop download at any time",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    else:
        processing_msg = await update.message.reply_text("📥 Downloading video... Please wait. Send /cancel to stop")
        
        file_path, error, caption = await download_tiktok_video(message_text)
        
        if error:
            await processing_msg.edit_text(
                f"❌ Failed to download video.\nError: {error}\n\n"
                "Please make sure the link is valid and try again."
            )
            return
        
        if not file_path or not os.path.exists(file_path):
            await processing_msg.edit_text("❌ Failed to download video. File not found.")
            return
        
        await processing_msg.delete()
        
        try:
            with open(file_path, "rb") as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=caption[:1024] if caption else "🎥 Downloaded without watermark!",
                    supports_streaming=True,
                )
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await update.message.reply_text("❌ Failed to send video. The file might be too large or corrupted.")
        finally:
            try:
                os.remove(file_path)
            except Exception as e:
                logger.error(f"Error deleting file: {e}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send help message"""
    help_text = (
        "*📚 Bot Commands & Usage*\n\n"
        "*Single Video Download:*\n"
        "Send any TikTok video link\n"
        "• Includes original caption\n"
        "• Shows view/like counts\n"
        "Example: https://vm.tiktok.com/xxxxxx/\n\n"
        "*Profile Download Options:*\n"
        "• 🆕 Last 5/10/20 Videos\n"
        "• 📅 Last 7, 14, 30 Days\n"
        "• 📅 Last 2, 3 Months\n"
        "• 🎬 ALL Videos (All available videos)\n\n"
        "*Commands:*\n"
        "/start - Start the bot\n"
        "/help - Show this help\n"
        "/download @username - Download user videos\n"
        "/cancel - Stop ongoing download\n"
        "/stats @username - View profile statistics\n\n"
        "*How to Cancel:*\n"
        "⚠️ Send /cancel at any time to stop downloading\n"
        "• Stops after current video finishes\n"
        "• Sends all videos downloaded so far\n\n"
        "*Why can I download ALL videos?*\n"
        "✅ The bot now fetches all videos from the profile\n"
        "✅ No more 20 video limit!\n"
        "✅ Downloads as many as TikTok provides (typically 50-200+)\n\n"
        "*Tips:*\n"
        "• No watermark on videos\n"
        "• Original captions preserved\n"
        "• Works with public profiles only\n"
        "• Videos are automatically deleted after sending\n"
        "• Use /cancel if download takes too long"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks for time period selection"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    # Clear any previous cancellation flag
    if user_id in download_cancelled:
        del download_cancelled[user_id]
    
    # Mark this user as having an active download
    active_downloads[user_id] = True
    
    if data.startswith("time_"):
        parts = data.split("_")
        username = parts[1]
        option = parts[2]
        
        # Create progress message
        progress_msg = await query.edit_message_text(
            f"📥 Preparing to download from @{username}...\n"
            f"⚠️ Send /cancel to stop at any time"
        )
        
        # Determine download parameters
        if option == "5videos":
            videos = await download_videos_by_timeframe(username, days=999, limit=5, user_id=user_id, update_msg=progress_msg)
        elif option == "10videos":
            videos = await download_videos_by_timeframe(username, days=999, limit=10, user_id=user_id, update_msg=progress_msg)
        elif option == "20videos":
            videos = await download_videos_by_timeframe(username, days=999, limit=20, user_id=user_id, update_msg=progress_msg)
        elif option == "7days":
            videos = await download_videos_by_timeframe(username, days=7, user_id=user_id, update_msg=progress_msg)
        elif option == "14days":
            videos = await download_videos_by_timeframe(username, days=14, user_id=user_id, update_msg=progress_msg)
        elif option == "30days":
            videos = await download_videos_by_timeframe(username, days=30, user_id=user_id, update_msg=progress_msg)
        elif option == "60days":
            videos = await download_videos_by_timeframe(username, days=60, user_id=user_id, update_msg=progress_msg)
        elif option == "90days":
            videos = await download_videos_by_timeframe(username, days=90, user_id=user_id, update_msg=progress_msg)
        elif option == "all":
            videos = await download_videos_by_timeframe(username, days=999, user_id=user_id, update_msg=progress_msg)
        else:
            await progress_msg.edit_text(f"❌ Invalid option")
            if user_id in active_downloads:
                del active_downloads[user_id]
            return
        
        # Remove from active downloads
        if user_id in active_downloads:
            del active_downloads[user_id]
        
        # Check if cancelled
        if download_cancelled.get(user_id, False):
            # Clear cancellation flag
            del download_cancelled[user_id]
            if videos:
                await progress_msg.edit_text(
                    f"⏹️ *Download Stopped!*\n\n"
                    f"Downloaded {len(videos)} videos from @{username}\n"
                    f"Sending downloaded videos...\n\n"
                    f"Use /download @{username} to continue!",
                    parse_mode="Markdown"
                )
            else:
                await progress_msg.edit_text(
                    f"⏹️ *Download Cancelled*\n\n"
                    f"No videos were downloaded.\n"
                    f"Use /download @{username} to start again!",
                    parse_mode="Markdown"
                )
                return
        
        if not videos:
            await progress_msg.edit_text(
                f"❌ No videos found from @{username}\n\n"
                "Possible reasons:\n"
                "- Profile is private\n"
                "- No videos in selected time period\n"
                "- Invalid username\n"
                "- Download was cancelled"
            )
            return
        
        await progress_msg.edit_text(
            f"✅ Downloaded {len(videos)} videos from @{username}!\n"
            f"📤 Sending videos now...\n\n"
            f"⏰ This may take a few moments."
        )
        
        # Send videos
        success_count = 0
        for idx, (file_path, title, caption) in enumerate(videos, 1):
            try:
                with open(file_path, "rb") as video_file:
                    await query.message.reply_video(
                        video=video_file,
                        caption=caption[:1024],
                        supports_streaming=True,
                    )
                success_count += 1
                logger.info(f"Sent video {idx}/{len(videos)} from @{username}")
            except Exception as e:
                logger.error(f"Error sending video {idx}: {e}")
            finally:
                try:
                    os.remove(file_path)
                except:
                    pass
            
            await asyncio.sleep(1)
        
        await query.message.reply_text(
            f"✅ *Complete!* Sent {success_count}/{len(videos)} videos from @{username}\n\n"
            f"📊 Total videos in profile: {len(videos)}\n"
            f"💡 Use /stats @{username} for detailed statistics!\n"
            f"🎬 Use /download @{username} again for more!\n"
            f"⏹️ Use /cancel to stop next download!",
            parse_mode="Markdown"
        )
        
        await progress_msg.delete()


def main() -> None:
    """Start the bot"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("download", download_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    # Add callback handler for buttons
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Add message handler for text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot is starting with CANCEL/STOP command support...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()