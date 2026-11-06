import os
import json
import subprocess
import glob
import re
import requests
from openai import OpenAI

# ================= 配置区域 =================
CHANNEL_URL = "https://www.youtube.com/@BTC-ouyang/videos" # ⚠️ 确保这里改了频道
N_RECENT = 5 
SEEN_FILE = "seen.json"
# ============================================

def generate_cookies_file():
    raw_cookie = os.environ.get("YOUTUBE_COOKIES_RAW", "").strip()
    if not raw_cookie:
        print("未找到 YOUTUBE_COOKIES_RAW，跳过生成 cookies.txt")
        return
    
    if "# Netscape" in raw_cookie or "\t" in raw_cookie:
        with open("cookies.txt", "w", encoding="utf-8") as f:
            f.write(raw_cookie)
    else:
        if raw_cookie.lower().startswith("cookie:"):
            raw_cookie = raw_cookie[7:].strip()
        lines = ["# Netscape HTTP Cookie File"]
        for item in raw_cookie.split(";"):
            if "=" in item:
                name, value = item.strip().split("=", 1)
                lines.append(f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}")
        with open("cookies.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    print("成功生成 cookies.txt 文件")

def get_recent_videos():
    # 增加了解析客户端参数，绕过云端 IP 风控
    cmd = [
        "yt-dlp", "--flat-playlist", "--print", "%(id)s|%(title)s", 
        "--cookies", "cookies.txt", 
        "--extractor-args", "youtube:player_client=web_safari,web_embedded",
        CHANNEL_URL
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    videos = []
    for line in result.stdout.strip().splitlines()[:N_RECENT]:
        vid, title = line.split("|", 1)
        videos.append((vid, title))
    return videos

def get_transcript(video_id: str) -> str:
    cmd = [
        "yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs",
        "--sub-langs", "zh-Hans,zh,en", "--sub-format", "vtt",
        "--cookies", "cookies.txt",
        "--extractor-args", "youtube:player_client=web_safari,web_embedded", # 强制使用网页客户端
        "-o", f"/tmp/{video_id}.%(ext)s",
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    
    # 关键修改：捕获并打印详细报错，不再直接被吞掉
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"--- yt-dlp 报错详情 ---")
        print(result.stderr)
        print(f"-----------------------")
        raise RuntimeError(f"yt-dlp 失败")

    files = sorted(glob.glob(f"/tmp/{video_id}*.vtt"))
    if not files:
        raise RuntimeError(f"{video_id} 未生成字幕文件（可能该视频真的没字幕）")
    lines = []
    for line in open(files[0], encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("WEBVTT") or "-->" in line or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return " ".join(lines)

def summarize(text: str) -> str:
    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "你是视频总结助手。用中文输出：1) 一句话核心主题；2) 3–5 个关键要点；3) 值得注意的结论。不要编造字幕中没有的信息。"},
            {"role": "user", "content": f"以下是视频字幕：\n\n{text[:12000]}"},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content

def push_wechat(title: str, content: str):
    resp = requests.post(
        "https://www.pushplus.plus/send",
        json={"token": os.environ["PUSHPLUS_TOKEN"], "title": title, "content": content, "template": "markdown"},
        timeout=30,
    )
    resp.raise_for_status()

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f: return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f: json.dump(sorted(seen), f)

def main():
    generate_cookies_file()
    seen = load_seen()
    processed = 0
    try:
        videos = get_recent_videos()
    except Exception as e:
        print(f"获取视频列表失败: {e}")
        return

    for video_id, title in videos:
        if video_id in seen: continue
        try:
            print(f"正在处理新视频: {title} ({video_id})")
            transcript = get_transcript(video_id)
            summary = summarize(transcript)
            push_wechat(f"新视频：{title}", summary)
            seen.add(video_id)
            save_seen(seen)
            processed += 1
            print(f"✅ 成功推送: {title}")
        except Exception as e:
            print(f"❌ 处理视频 {video_id} 时出错: {e}")
            continue

    if processed == 0:
        print("本次运行没有发现新视频，静默退出。")
    else:
        print(f"本次运行处理了 {processed} 个新视频。")

if __name__ == "__main__":
    main()
