from __future__ import annotations
import os
import copy
import gradio as gr
from dotenv import load_dotenv
import http.client
import json
import re
import random
import uuid
import prompts  # 鍔ㄦ€佽鍙栧綋鍓嶈瑷€涓庡垎绫绘彁绀鸿瘝
from typing import Any, List
from music_db import MusicDatabase
from memory_manager import MemoryManager
from gim_audio_agent import GIMAudioAgent  # 鏂板锛氬鍏IM闊抽缂栬緫浠ｇ悊
from baseline_retriever import retrieve_baseline_track
from kimusic_client import create_proxy_generated_track
from kimusic_renderer import render_from_session_data
from prompts import get_full_system_prompt, MUSIC_NOTE, MUSIC_SELECTION_INFORMATION_TEMPLATE
from prompts import get_music_system_prompt, get_music_user_prompt
from hyper_parameters import (
    NUM_MUSIC_TRACKS, 
    MAX_MEMORY_ITEMS, 
    MAX_CONVERSATION_LENGTH,
    SUMMARIZATION_THRESHOLD,
    MODEL_NAME,
    MAX_TOKENS_RESPONSE,
    MAX_TOKENS_SUMMARY,
    MOOD_OPTIONS_NUM,
    GENRE_OPTIONS_NUM,
    REBUILD_MUSIC_INDEX
)
import time
from datetime import datetime
from session_logger import (
    append_ablation_session_log,
    add_music_track,
    create_empty_session,
    log_message,
    prepare_session_metadata,
    save_llm_estimated_va,
    save_session_json,
    update_music_track_feedback,
)
from session_manager import (
    generate_participant_id,
    get_next_session_info,
    init_session as init_ablation_session,
    start_washout,
)

try:
    import gradio_client.utils as _gcu

    _orig_json2py = _gcu._json_schema_to_python_type

    def _json_schema_to_python_type_patched(schema, defs=None):
        # JSON Schema supports boolean schemas:
        # True  => any schema allowed
        # False => no schema allowed (we still treat as Any to avoid crashing)
        if schema is True or schema is False:
            return "Any"
        return _orig_json2py(schema, defs)

    _gcu._json_schema_to_python_type = _json_schema_to_python_type_patched
except Exception as _e:
    # If patch fails, we don't block app startup; it will just error as before.
    pass

# Load environment variables
load_dotenv()

# 鍦ㄦ枃浠堕《閮ㄦ坊鍔犵姸鎬佽窡韪彉閲?PREVIOUS_STATE = None
CURRENT_STATE = None
# 鍏ㄥ眬鍙橀噺锛岀敤浜庢帶鍒禪I鏇存柊
SHOULD_UPDATE_MUSIC_UI = False

PANAS_ITEM_LABELS = {
    "interested": {"en": "Interested", "zh": "鎰熷叴瓒?},
    "distressed": {"en": "Distressed", "zh": "蹇冪儲"},
    "excited": {"en": "Excited", "zh": "鍏村"},
    "upset": {"en": "Upset", "zh": "闅惧彈"},
    "strong": {"en": "Strong", "zh": "寮鸿€屾湁鍔?},
    "guilty": {"en": "Guilty", "zh": "鍐呯枤"},
    "scared": {"en": "Scared", "zh": "瀹虫€?},
    "hostile": {"en": "Hostile", "zh": "鏁屾剰"},
    "enthusiastic": {"en": "Enthusiastic", "zh": "鐑儏"},
    "proud": {"en": "Proud", "zh": "鑷豹"},
    "irritable": {"en": "Irritable", "zh": "鐑﹁簛"},
    "alert": {"en": "Alert", "zh": "璀﹁"},
    "ashamed": {"en": "Ashamed", "zh": "缇炴劎"},
    "inspired": {"en": "Inspired", "zh": "鍙楅紦鑸?},
    "nervous": {"en": "Nervous", "zh": "绱у紶"},
    "determined": {"en": "Determined", "zh": "鍧氬畾"},
    "attentive": {"en": "Attentive", "zh": "涓撴敞"},
    "jittery": {"en": "Jittery", "zh": "鍧愮珛涓嶅畨"},
    "active": {"en": "Active", "zh": "娲昏穬"},
    "afraid": {"en": "Afraid", "zh": "鎭愭儳"},
}

PANAS_POSITIVE_ITEMS = [
    "interested", "excited", "strong", "enthusiastic", "proud",
    "alert", "inspired", "determined", "attentive", "active"
]

PANAS_NEGATIVE_ITEMS = [
    "distressed", "upset", "guilty", "scared", "hostile",
    "irritable", "ashamed", "nervous", "jittery", "afraid"
]

PANAS_ITEM_KEYS = list(PANAS_ITEM_LABELS.keys())

SUS_ITEMS = [
    ("I think that I would like to use this system frequently", "鎴戞兂鎴戜細缁忓父浣跨敤杩欎釜绯荤粺"),
    ("I found the system unnecessarily complex", "鎴戝彂鐜拌绯荤粺杩囦簬澶嶆潅"),
    ("I thought the system was easy to use", "鎴戣涓鸿绯荤粺鏄撲簬浣跨敤"),
    ("I think that I would need the support of a technical person to use this system", "鎴戣涓烘垜闇€瑕佹妧鏈汉鍛樼殑甯姪鎵嶈兘浣跨敤杩欎釜绯荤粺"),
    ("I found the various functions in this system were well integrated", "鎴戝彂鐜拌绯荤粺鑳藉寰堝ソ鍦伴泦鎴愪簡鍚勭鍔熻兘"),
    ("I thought there was too much inconsistency in this system", "鎴戣涓鸿绯荤粺涓瓨鍦ㄥぇ閲忕殑涓嶄竴鑷?),
    ("I would imagine that most people would learn to use this system very quickly", "鎴戞兂澶у鏁扮敤鎴疯兘寰堝揩瀛︿細浣跨敤璇ョ郴缁?),
    ("I found the system very cumbersome to use", "鎴戝彂鐜拌绯荤粺浣跨敤璧锋潵寰堥夯鐑?),
    ("I felt very confident using the system", "鎴戜娇鐢ㄨ绯荤粺鏃讹紝鎰熷埌寰堟湁淇″績"),
    ("I needed to learn a lot of things before I could get going with this system", "鍦ㄤ娇鐢ㄨ绯荤粺涔嬪墠锛屾垜闇€瑕佸涔犲緢澶氱浉鍏崇煡璇?),
]

THERAPY_EXPERIENCE_ITEMS = [
    ("T1_empathy", "AI therapist made me feel understood and supported", "AI 娌荤枟甯堢殑瀵硅瘽璁╂垜鎰熷埌琚悊瑙ｅ拰鏀寔"),
    ("T2_music_match", "The selected music matched my emotional state", "绯荤粺閫夋嫨鐨勯煶涔愮鍚堟垜褰撴椂鐨勬儏缁姸鎬?),
    ("T3_imagery_facilitation", "The music helped me generate meaningful imagery or associations", "闊充箰甯姪鎴戜骇鐢熶簡鏈夋剰涔夌殑鐢婚潰鎴栬仈鎯?),
    ("T4_interpretation_quality", "The AI's interpretation of my imagery made sense", "AI 瀵规垜鎰忚薄鐨勮В璇绘槸鏈夐亾鐞嗙殑"),
    ("T5_flow_comfort", "The pace and flow of the therapy felt comfortable", "鏁翠釜娌荤枟杩囩▼鐨勮妭濂忓拰娴佺▼璁╂垜鎰熷埌鑸掗€?),
    ("T6_reuse_intention", "I would use this system again for relaxation or self-exploration", "鎴戞効鎰忓啀娆′娇鐢ㄨ繖涓郴缁熻繘琛屾斁鏉炬垨鑷垜鎺㈢储"),
]


def get_ui_texts(is_chinese: bool):
    if is_chinese:
        return {
            "input_label": "鍒嗕韩鎮ㄧ殑鎯虫硶...",
            "input_placeholder": "鍦ㄦ杈撳叆鎮ㄧ殑娑堟伅...",
            "submit": "鍙戦€?,
            "finish_session": "缁撴潫瀵硅瘽",
            "sam_title_pre": "### 浼氬墠 SAM 璇勫垎",
            "sam_title_post": "### 浼氬悗 SAM 璇勫垎",
            "sam_title_default": "### SAM 璇勫垎",
            "sam_valence_label": "鎰夋偊搴︼紙Valence锛?,
            "sam_valence_info": "鎮ㄧ幇鍦ㄦ劅瑙夋槸鏇存剦蹇紝杩樻槸鏇翠笉鎰夊揩锛?,
            "sam_arousal_label": "鍞ら啋搴︼紙Arousal锛?,
            "sam_arousal_info": "鎮ㄧ幇鍦ㄦ槸鏇村钩闈欐斁鏉撅紝杩樻槸鏇村叴濂嬬揣寮狅紵",
            "sam_instruction": "Valence锛堟剦鎮﹀害锛? 1=闈炲父涓嶆剦蹇? 5=涓€? 9=闈炲父鎰夊揩\n\nArousal锛堝敜閱掑害锛? 1=闈炲父骞抽潤, 5=涓瓑, 9=闈炲父鍏村",
            "sam_submit": "鎻愪氦 SAM",
            "sam_saved": "SAM 璇勫垎宸蹭繚瀛樸€?,
            "sam_not_needed": "褰撳墠鏃犻渶璇勫垎銆?,
            "panas_title_pre": "### 浼氬墠 PANAS 璇勫垎",
            "panas_title_post": "### 浼氬悗 PANAS 璇勫垎",
            "panas_title_default": "### PANAS 璇勫垎",
            "panas_submit": "鎻愪氦 PANAS",
            "panas_saved": "PANAS 璇勫垎宸蹭繚瀛樸€?,
            "panas_not_needed": "褰撳墠鏃犻渶 PANAS 璇勫垎銆?,
            "sus_title": "### SUS 绯荤粺鍙敤鎬ч噺琛?,
            "sus_title_default": "### SUS 绯荤粺鍙敤鎬ч噺琛?,
            "sus_submit": "鎻愪氦 SUS",
            "sus_saved": "SUS 宸蹭繚瀛樸€?,
            "sus_not_needed": "褰撳墠鏃犻渶 SUS銆?,
            "therapy_title": "### 娌荤枟浣撻獙涓撻」璇勪及",
            "therapy_title_default": "### 娌荤枟浣撻獙涓撻」璇勪及",
            "therapy_submit": "鎻愪氦浣撻獙璇勪及",
            "therapy_saved": "娌荤枟浣撻獙璇勪及宸蹭繚瀛樸€?,
            "therapy_not_needed": "褰撳墠鏃犻渶娌荤枟浣撻獙璇勪及銆?,
            "music_start": "鎴戞鍦ㄤ负鎮ㄥ噯澶囧悎閫傜殑闊充箰锛岃绋嶇瓑鐗囧埢...",
            "music_processing": "鎴戞鍦ㄥ鐞嗛煶涔愶紝璇风◢绛夌墖鍒汇€?,
            "music_ready": "闊充箰宸插噯澶囧ソ锛屾偍鍙互寮€濮嬭亞鍚€?,
            "music_experience": "鎺ヤ笅鏉ヨ鍏堝畨闈欐劅鍙楅煶涔愶紝鎴戜細鍦ㄧ粨鏉熷悗缁х画闄偍銆?,
            "music_ended": "杩欐闊充箰宸茬粡缁撴潫銆傚厛鎱㈡參鍥炲埌褰撲笅锛屾垜浠啀涓€璧峰洖椤惧垰鎵嶇殑浣撻獙銆?,
            "post_music_reflection": "娆㈣繋鍥炴潵銆傚厛鎱㈡參鍥炲埌褰撲笅锛屼笉鐫€鎬ラ┈涓婂洖绛斻€俓n鍙互鍏堟敞鎰忎竴涓嬭嚜宸辩殑鍛煎惛銆佽韩浣撶殑鎰熻锛屾垨鑰呮鍒绘渶鏄庢樉鐨勬儏缁€俓n\n褰撴偍鍑嗗濂界殑鏃跺€欙紝鍙互鎱㈡參鍥炴兂涓€涓嬶紝\n鍒氭墠鐨勯煶涔愪綋楠岄噷锛屾湁娌℃湁浠€涔堢敾闈€佹儏缁€佹兂娉曟垨韬綋鎰熻璁╂偍鍗拌薄鐗瑰埆娣卞埢锛?,
            "closure": "鎴戜滑宸茬粡鎺ヨ繎杩欐鐤楁剤浣撻獙鐨勫熬澹颁簡銆俓n濡傛灉鎮ㄦ効鎰忥紝鎴戜滑鍙互涓€璧风畝鍗曟暣鐞嗕竴涓嬪垰鎵嶇殑浣撻獙锛岀劧鍚庡畬鎴愭渶鍚庣殑璇勪及銆?,
            "post_session_assessment_intro": "鍦ㄧ粨鏉熶箣鍓嶏紝鎴戜滑浼氬仛涓€涓畝鐭殑鎰熷彈璇勪及锛孿n甯姪鎴戜滑鏇村ソ鍦扮悊瑙ｈ繖娆′綋楠屽鎮ㄧ殑褰卞搷銆俓n璇锋牴鎹偍姝ゅ埢鐨勬劅鍙楄繘琛岄€夋嫨锛屾病鏈夊閿欎箣鍒嗐€?,
            "play_music": "馃帶 璇蜂娇鐢ㄤ笅鏂规挱鏀惧櫒鑱嗗惉闊充箰銆?,
            "participant_id_label": "鍙備笌鑰呯紪鍙?,
            "participant_id_placeholder": "绯荤粺浼氳嚜鍔ㄧ敓鎴愶紱绗簩娆′細璇濇椂鍙矘璐村凡淇濆瓨鐨勭紪鍙?,
            "start_session": "寮€濮嬪疄楠?,
            "participant_id_generated": "鍙備笌鑰呯紪鍙峰凡鐢熸垚銆傜 {session_number}/2 娆′細璇濆凡鍑嗗濂姐€傝淇濆瓨姝ょ紪鍙蜂互渚垮畬鎴愮浜屾浼氳瘽銆?,
            "all_sessions_complete": "鎵€鏈夋寚瀹氫細璇濆潎宸插畬鎴愩€傝阿璋㈡偍銆?,
            "session_ready": "绗?{session_number}/2 娆′細璇濆凡鍑嗗濂姐€?,
            "save_participant_id": "璇蜂繚瀛樻偍鐨勫弬涓庤€呯紪鍙凤細\n\n**{participant_id}**\n\n鎮ㄩ渶瑕佷娇鐢ㄥ畠瀹屾垚绗簩娆′細璇濄€?,
            "washout_continue": "璇峰厛瀹屾垚浼戞伅鍐嶈繘鍏ョ浜屾浼氳瘽銆傚墿浣欐椂闂达細{minutes}:{seconds:02d}銆傛偍鐨勫弬涓庤€呯紪鍙锋槸 {participant_id}銆?,
            "washout_title": "璇峰湪寮€濮嬬浜屾浼氳瘽鍓嶇煭鏆備紤鎭?5 鍒嗛挓銆?,
            "washout_id_intro": "鎮ㄧ殑鍙備笌鑰呯紪鍙锋槸锛?,
            "washout_instruction": "璇蜂繚瀛樻缂栧彿銆傚€掕鏃剁粨鏉熷悗锛岀浜屾浼氳瘽灏嗗彲缁х画銆?,
            "washout_available_after": "鍙户缁椂闂达細{end_time}",
            "washout_calculating": "姝ｅ湪璁＄畻鍓╀綑鏃堕棿...",
            "washout_done": "浼戞伅宸插畬鎴愩€傛偍鍙互鐐瑰嚮鈥滃紑濮嬩細璇濃€濈户缁浜屾浼氳瘽銆?,
            "washout_remaining": "鍓╀綑浼戞伅鏃堕棿锛?,
            "washout_complete": "浼戞伅宸插畬鎴愩€?,
            "audio_label": "闊充箰鎾斁鍣?,
            "final_complete_title": "璋㈣阿鎮紝鎮ㄥ凡缁忓畬鎴愪袱娆′細璇濄€?,
            "final_complete_body": "鎮ㄧ殑鍥炵瓟宸蹭繚瀛樸€?,
            "start_session_2": "寮€濮嬬浜屾浼氳瘽"
        }
    return {
        "input_label": "Share your thoughts...",
        "input_placeholder": "Type your message here...",
        "submit": "Send",
        "finish_session": "Finish Session",
        "sam_title_pre": "### Pre-session SAM Rating",
        "sam_title_post": "### Post-session SAM Rating",
        "sam_title_default": "### SAM Rating",
        "sam_valence_label": "Valence",
        "sam_valence_info": "How pleasant or unpleasant do you feel right now?",
        "sam_arousal_label": "Arousal",
        "sam_arousal_info": "How calm or excited do you feel right now?",
        "sam_instruction": "Valence: 1=Very unpleasant, 5=Neutral, 9=Very pleasant\n\nArousal: 1=Very calm, 5=Moderate, 9=Very excited",
        "sam_submit": "Submit SAM",
        "sam_saved": "SAM rating saved.",
        "sam_not_needed": "No SAM rating is required right now.",
        "panas_title_pre": "### Pre-session PANAS Rating",
        "panas_title_post": "### Post-session PANAS Rating",
        "panas_title_default": "### PANAS Rating",
        "panas_submit": "Submit PANAS",
        "panas_saved": "PANAS rating saved.",
        "panas_not_needed": "No PANAS rating is required right now.",
        "sus_title": "### System Usability Scale",
        "sus_title_default": "### System Usability Scale",
        "sus_submit": "Submit SUS",
        "sus_saved": "SUS saved.",
        "sus_not_needed": "No SUS rating is required right now.",
        "therapy_title": "### Therapy Experience",
        "therapy_title_default": "### Therapy Experience",
        "therapy_submit": "Submit Experience",
        "therapy_saved": "Therapy experience saved.",
        "therapy_not_needed": "No therapy experience rating is required right now.",
        "music_start": "I'm preparing suitable music for you, please wait a moment...",
        "music_processing": "I am processing the music now. Please wait a moment.",
        "music_ready": "The music is ready. You can start listening.",
        "music_experience": "Please take a moment to experience the music. I will continue with you afterward.",
        "music_ended": "The music has ended. Take a moment to return to the present, and then we can reflect on your experience together.",
        "post_music_reflection": "Welcome back. Take your time returning to the present moment.\nYou do not need to answer immediately. You can first notice your breath, your body, or the most noticeable feeling right now.\n\nWhen you feel ready, you can gently reflect on the music experience.\nWas there any image, emotion, thought, or bodily sensation that stood out to you?",
        "closure": "We are approaching the end of this therapeutic experience.\nIf you would like, we can briefly reflect on what you experienced and then complete the final assessment.",
        "post_session_assessment_intro": "Before we finish, we will do a brief assessment\nto better understand how this experience has affected you.\nPlease respond based on how you feel right now.\nThere are no right or wrong answers.",
        "play_music": "馃帶 Please use the player below to listen to the music.",
        "participant_id_label": "Participant ID",
        "participant_id_placeholder": "Generated automatically, or paste saved ID for Session 2",
        "start_session": "Start Experiment",
        "participant_id_generated": "Participant ID generated. Session {session_number} of 2 is ready. Please save this ID for Session 2.",
        "all_sessions_complete": "All assigned sessions are complete. Thank you.",
        "session_ready": "Session {session_number} of 2 is ready.",
        "save_participant_id": "Please save your Participant ID:\n\n**{participant_id}**\n\nYou will need it to complete Session 2.",
        "washout_continue": "Please continue your break before Session 2. Remaining time: {minutes}:{seconds:02d}. Your Participant ID is {participant_id}.",
        "washout_title": "Please take a short 5-minute break before starting Session 2.",
        "washout_id_intro": "Your Participant ID is:",
        "washout_instruction": "Please save this ID. Session 2 will become available when the countdown finishes.",
        "washout_available_after": "Available after: {end_time}",
        "washout_calculating": "Calculating remaining time...",
        "washout_done": "The break is complete. You may click Start Session to continue to Session 2.",
        "washout_remaining": "Remaining break time:",
        "washout_complete": "Break complete.",
        "audio_label": "Music Player",
        "final_complete_title": "Thank you. You have completed both sessions.",
        "final_complete_body": "Your responses have been saved.",
        "start_session_2": "Start Session 2"
    }

class GIMState:
    PRELUDE = "prelude"
    INDUCTION = "induction"
    MUSIC_IMAGING = "music_imaging"
    POSTLUDE = "postlude"


SESSION_OVER_TOKEN = "<session_over>"
SESSION_OVER_PROMPT_INSTRUCTION = (
    "\n\nPOSTLUDE SESSION CONTROL:\n"
    "Append the control token <session_over> on a separate final line ONLY IF "
    "all conditions are satisfied: 1. The participant's experience has been "
    "summarized. 2. The experience has been connected back to the participant's "
    "original concerns or current life. 3. A clear closing / return-to-present "
    "statement has been given. 4. NO further reflective question is asked. "
    "If the assistant is still asking questions, inviting further exploration, "
    "or continuing the conversation, DO NOT emit <session_over>. Do not explain "
    "this token to the participant."
)
MUSIC_IMAGING_MIN_USER_TURNS = 2
MUSIC_IMAGING_MAX_USER_TURNS = 4
POSTLUDE_MAX_USER_TURNS = 3


def is_session_chinese(session):
    return getattr(session, "language", "en") == "zh"


class GIMTherapySession:
    def __init__(
        self,
        startup_light: bool = False,
        user_id: str = None,
        condition: str = None,
        condition_order: list = None,
        session_number: int = 1,
        session_id: str = None,
        language: str = "en",
    ):
        init_started_at = time.time()
        print(f"[startup] GIMTherapySession.__init__ start startup_light={startup_light}")
        self.startup_light = startup_light
        self.current_state = GIMState.PRELUDE
        self.chat_history = []
        self.timestamp_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.language = language or "en"
        import hyper_parameters
        default_condition = "kimusic" if hyper_parameters.USE_KIMUSIC_GENERATION else "baseline"
        self.user_id = user_id or generate_participant_id()
        self.condition = condition or default_condition
        self.condition_order = condition_order or [self.condition]
        self.session_number = session_number
        self.session_data = create_empty_session(user_id=self.user_id, condition=self.condition)
        if session_id:
            self.session_data["session_id"] = session_id
        self.session_data["condition_order"] = list(self.condition_order)
        self.session_data["session_number"] = self.session_number
        self.session_data["timestamp_start"] = self.timestamp_start
        self.focus_intention = None
        # Initialize with welcome message
        welcome_started_at = time.time()
        self.initialize_welcome_message()
        print(f"[startup] initialize_welcome_message completed in {time.time() - welcome_started_at:.3f}s")
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        # 鍙互閫氳繃鐜鍙橀噺鎺у埗鏄惁閲嶅缓绱?        music_db_started_at = time.time()
        print(f"[startup] MusicDatabase construction start use_elasticsearch={not startup_light}")
        self.music_db = MusicDatabase("../toy_dataset/music_data_complete_with_valence_arousal.json", use_elasticsearch=not startup_light, rebuild_index=True) if not startup_light else None
        if startup_light:
            print("[startup] MusicDatabase deferred (lazy)")
        else:
            print(f"[startup] MusicDatabase construction completed in {time.time() - music_db_started_at:.3f}s")
        self.user_mood = None
        self.music_selected = False
        memory_started_at = time.time()
        print("[startup] MemoryManager construction start")
        self.memory = MemoryManager(max_memory_items=MAX_MEMORY_ITEMS, max_conversation_length=MAX_CONVERSATION_LENGTH) if not startup_light else None
        if startup_light:
            print("[startup] MemoryManager deferred (lazy)")
        else:
            print(f"[startup] MemoryManager construction completed in {time.time() - memory_started_at:.3f}s")
        self.last_assistant_message = None
        self.summarization_threshold = SUMMARIZATION_THRESHOLD
        self.selected_music_tracks = []  # Store selected music tracks
        
        # 鍒濆鍖栬繘搴︽樉绀虹浉鍏冲睘鎬?        self.progress_status = ""  # 杩涘害鐘舵€佹枃鏈?        self.phase_info = ""      # 闃舵淇℃伅
        self.music_info = ""      # 闊充箰澶勭悊淇℃伅
        
        # 鏂板锛氬垵濮嬪寲GIM闊抽缂栬緫浠ｇ悊
        audio_agent_started_at = time.time()
        print("[startup] GIMAudioAgent construction start")
        self.audio_agent = GIMAudioAgent(
            music_db=self.music_db,
            api_key=self.api_key,
            music_root="../toy_dataset/mp3",  ##todo锛氬叏鍙usic root鍜宱utput dir
            output_dir="output"
        ) if not startup_light else None
        if startup_light:
            print("[startup] GIMAudioAgent deferred (lazy)")
        else:
            print(f"[startup] GIMAudioAgent construction completed in {time.time() - audio_agent_started_at:.3f}s")
        self.gim_program_result = None  # 瀛樺偍鏈€缁堢殑Program鍚堟垚缁撴灉
        self.kimusic_render_result = None
        self.baseline_used_track_ids = set()
        self.ablation_logged = False
        self.washout_pending = False
        self.experiment_started = False
        self.sam_state = {
            "ratings": [],
            "pending_phase": "pre_session",
            "pre_session_done": False,
            "post_session_done": False
        }
        self.session_completed = False
        self.panas_state = {
            "panas": [],
            "pending_phase": None,
            "pre_session_done": False,
            "post_session_done": False,
            "current_order": [],
            "current_order_phase": None
        }
        self.ui_message_flags = {
            "music_generation_started": False,
            "music_processing_started": False,
            "music_ready": False,
            "music_experience": False,
            "music_ended": False,
            "post_music_reflection": False,
            "closure": False,
            "final_assessment_intro": False
        }
        self.postlude_reflection_prompt_index = None
        self.phase_user_turns = {
            GIMState.PRELUDE: 0,
            GIMState.INDUCTION: 0,
            GIMState.MUSIC_IMAGING: 0,
            GIMState.POSTLUDE: 0,
        }
        print(f"[startup] GIMTherapySession.__init__ completed in {time.time() - init_started_at:.3f}s")

    def render_kimusic_track_once(self, proxy_track, fallback_audio_path):
        if self.kimusic_render_result is not None:
            return self.kimusic_render_result

        render_payload = copy.deepcopy(self.session_data)
        render_payload["generated_music"] = copy.deepcopy(proxy_track.get("generated_music"))
        render_payload["music_sequence"] = [copy.deepcopy(proxy_track)]

        self.kimusic_render_result = render_from_session_data(
            render_payload,
            output_dir="output/kimusic_generated",
            output_stem=f"{self.session_data.get('session_id', 'session')}_track0"
        )

        generated_music = proxy_track.setdefault("generated_music", {})
        if self.kimusic_render_result.get("success"):
            audio_file = self.kimusic_render_result.get("mp3_path")
            audio_file_full_path = os.path.abspath(audio_file)
            generated_music["audio_file"] = audio_file
            proxy_track["file_path"] = audio_file
            proxy_track["full_path"] = audio_file_full_path
            proxy_track["filename"] = os.path.basename(audio_file_full_path)
        else:
            generated_music["render_error"] = self.kimusic_render_result.get("error")
            proxy_track["file_path"] = fallback_audio_path
            proxy_track["full_path"] = os.path.abspath(fallback_audio_path) if fallback_audio_path else None
            proxy_track["filename"] = os.path.basename(fallback_audio_path) if fallback_audio_path else None
            if fallback_audio_path and os.path.exists(fallback_audio_path):
                generated_music["audio_file"] = os.path.relpath(os.path.abspath(fallback_audio_path)).replace(os.sep, "/")
            else:
                generated_music["audio_file"] = None

        return self.kimusic_render_result

    def ensure_music_db(self):
        if self.music_db is None:
            music_db_started_at = time.time()
            print(f"[startup] MusicDatabase construction start use_elasticsearch={not self.startup_light}")
            self.music_db = MusicDatabase(
                "../toy_dataset/music_data_complete_with_valence_arousal.json",
                use_elasticsearch=not self.startup_light,
                rebuild_index=True
            )
            print(f"[startup] MusicDatabase construction completed in {time.time() - music_db_started_at:.3f}s")
        return self.music_db

    def ensure_memory_manager(self):
        if self.memory is None:
            memory_started_at = time.time()
            print("[startup] MemoryManager construction start")
            self.memory = MemoryManager(
                max_memory_items=MAX_MEMORY_ITEMS,
                max_conversation_length=MAX_CONVERSATION_LENGTH
            )
            print(f"[startup] MemoryManager construction completed in {time.time() - memory_started_at:.3f}s")
        return self.memory

    def ensure_audio_agent(self):
        if self.audio_agent is None:
            audio_agent_started_at = time.time()
            print("[startup] GIMAudioAgent construction start")
            self.audio_agent = GIMAudioAgent(
                music_db=self.ensure_music_db(),
                api_key=self.api_key,
                music_root="../toy_dataset/mp3",
                output_dir="output"
            )
            print(f"[startup] GIMAudioAgent construction completed in {time.time() - audio_agent_started_at:.3f}s")
        return self.audio_agent

    def has_llm_estimated_va(self):
        va_data = self.session_data.get("llm_estimated_va", {})
        return va_data.get("current_state") is not None and va_data.get("target_state") is not None

    def _extract_json_block(self, text: str):
        if not isinstance(text, str):
            return None
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return match.group(0) if match else None

    def _clamp_va_value(self, value):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return max(-1.0, min(1.0, numeric))

    def _parse_va_payload(self, text: str):
        json_block = self._extract_json_block(text)
        if not json_block:
            return None, None

        try:
            payload = json.loads(json_block)
        except Exception:
            return None, None

        def normalize_va(va_obj):
            if not isinstance(va_obj, dict):
                return None
            valence = self._clamp_va_value(va_obj.get("valence"))
            arousal = self._clamp_va_value(va_obj.get("arousal"))
            if valence is None or arousal is None:
                return None
            return {
                "valence": valence,
                "arousal": arousal
            }

        return normalize_va(payload.get("current_state_va")), normalize_va(payload.get("target_state_va"))

    def extract_va_from_prelude(self, user_message):
        if self.has_llm_estimated_va():
            return

        if self.current_state != GIMState.PRELUDE:
            return

        va_system_prompt = (
            prompts.PRELUDE_VA_EXTRACTION_PROMPT_ZH
            if getattr(prompts, "LANGUAGE", "en") == "zh"
            else prompts.PRELUDE_VA_EXTRACTION_PROMPT_EN
        )

        va_messages = [
            {
                "role": "system",
                "content": va_system_prompt
            },
            {
                "role": "user",
                "content": f"Conversation history: {self.chat_history[-8:] if len(self.chat_history) >= 8 else self.chat_history}\n\nUser's latest input: {user_message}"
            }
        ]

        conn = http.client.HTTPSConnection("api.openai.com")
        payload = json.dumps({
            "model": MODEL_NAME,
            "max_tokens": 120,
            "temperature": 0.1,
            "messages": va_messages
        })
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        try:
            conn.request("POST", "/v1/chat/completions", payload, headers)
            response = conn.getresponse()
            response_data = json.loads(response.read().decode("utf-8"))

            va_text = None
            if isinstance(response_data, dict):
                if 'choices' in response_data and response_data['choices']:
                    va_text = response_data['choices'][0]['message'].get('content')
                elif 'content' in response_data and response_data['content']:
                    va_text = response_data['content'][0].get('text')

            current_va, target_va = self._parse_va_payload(va_text)
            if current_va is not None and target_va is not None:
                save_llm_estimated_va(self.session_data, current_va, target_va)
                print(f"[va] extracted current={current_va} target={target_va}")
        except Exception as e:
            print(f"[va] extraction failed: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def get_logging_phase(self):
        phase = getattr(self, "current_state", None)
        if phase in (GIMState.PRELUDE, GIMState.INDUCTION, GIMState.MUSIC_IMAGING, GIMState.POSTLUDE):
            return phase
        return "system"

    def _normalize_message_content(self, content):
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            if isinstance(content.get("text"), str):
                return content["text"]
            if isinstance(content.get("content"), str):
                return content["content"]
            try:
                return json.dumps(content, ensure_ascii=False)
            except Exception:
                return str(content)
        if isinstance(content, (list, tuple)):
            parts = []
            for item in content:
                normalized_item = self._normalize_message_content(item)
                if normalized_item:
                    parts.append(normalized_item)
            return " ".join(parts)
        if content is None:
            return ""
        return str(content)

    def append_chat_message(self, role: str, content, phase: str = None):
        normalized_content = self._normalize_message_content(content)
        message = {"role": role, "content": normalized_content}
        self.chat_history.append(message)
        log_message(self.session_data, role, normalized_content, phase or self.get_logging_phase())
        return message

    def strip_session_over_token(self, text: str):
        if not isinstance(text, str):
            return text
        return text.replace(SESSION_OVER_TOKEN, "").strip()

    def response_still_invites_continuation(self, text: str):
        if not isinstance(text, str):
            return False
        normalized = text.lower()
        continuation_markers = [
            "?",
            "锛?,
            "浠€涔堟劅瑙?,
            "鏈変粈涔堝彉鍖?,
            "浣犳敞鎰忓埌",
            "浣犳効鎰?,
            "浣犲彲浠ユ參鎱㈡劅鍙?,
            "娴犫偓娑斿牊鍔呯憴",
            "閺堝绮堟稊鍫濆綁閸?,
            "娴ｇ姵鏁為幇蹇撳煂",
            "娴ｇ姵鍔归幇",
            "娴ｇ姴褰叉禒銉﹀弮閹便垺鍔呴崣",
            "what do you notice",
            "how does it feel",
            "what happens",
            "would you like",
        ]
        return any(marker in normalized for marker in continuation_markers)

    def mark_ready_for_post_session_assessment(self):
        self.session_completed = True
        self.sam_state["pending_phase"] = "post_session"
        self.panas_state["pending_phase"] = None
        self.panas_state["current_order"] = []
        self.panas_state["current_order_phase"] = None

    def append_music_tracks_to_session_data(self):
        import hyper_parameters

        prepare_session_metadata(
            self.session_data,
            n_tracks=max(4, len(self.selected_music_tracks or []))
        )
        existing_indexes = {
            entry.get("track_index") for entry in self.session_data.get("music_sequence", [])
        }
        for idx, track in enumerate(self.selected_music_tracks or [], start=1):
            if idx in existing_indexes:
                continue
            if self.condition == "kimusic" and track.get("source") != "generated":
                waypoint_sequence = self.session_data.get("waypoint_sequence") or []
                waypoint_va = (
                    waypoint_sequence[min(idx - 1, len(waypoint_sequence) - 1)]
                    if waypoint_sequence
                    else self.session_data.get("target_state_va") or self.session_data.get("current_state_va")
                )
                fallback_audio_path = track.get("full_path") or track.get("file_path") or track.get("filename")
                proxy_track = create_proxy_generated_track(
                    waypoint_va=waypoint_va,
                    session_id=self.session_data.get("session_id"),
                    track_index=idx - 1,
                    fallback_audio_path=fallback_audio_path,
                    waypoint_sequence=waypoint_sequence
                )
                proxy_track["duration_seconds"] = track.get("duration_seconds")
                track = {**track, **proxy_track}
            add_music_track(self.session_data, idx, track)

    def update_latest_track_feedback_from_reflection(self, imagery_text=None):
        music_sequence = self.session_data.get("music_sequence", [])
        if not music_sequence:
            return

        track_index = music_sequence[-1].get("track_index")
        latest_music_sam = None
        for rating in reversed(self.sam_state.get("ratings", [])):
            if rating.get("phase") not in ("pre_session", "post_session"):
                latest_music_sam = rating
                break

        update_music_track_feedback(
            self.session_data,
            track_index,
            latest_music_sam.get("valence") if latest_music_sam else None,
            latest_music_sam.get("arousal") if latest_music_sam else None,
            None,
            imagery_text if imagery_text else None
        )
        
    def initialize_welcome_message(self):
        """Initialize the session with a welcome message"""
        print("[startup] initialize_welcome_message start (static welcome text)")
        welcome_message_en = """Hello! I'm your GIM (Guided Imagery and Music) therapy assistant. I'm here to guide you through a therapeutic journey combining music and imagery. 

I specialize in:
鈥?Creating a safe and supportive environment for emotional exploration
鈥?Using music to facilitate deep personal insights
鈥?Guiding you through different phases of the GIM experience

Feel free to share whatever comes to your mind. Would you like to introduce yourself and tell me what brings you here today?"""

        welcome_message_zh = """鎮ㄥソ锛佹垜鏄偍鐨凣IM锛堝紩瀵煎紡闊充箰涓庢剰璞★級娌荤枟鍔╂墜銆傛垜灏嗛櫔浼存偍灞曞紑涓€娈电粨鍚堥煶涔愪笌鎰忚薄鐨勬不鐤椾箣鏃呫€?
鎴戠殑涓撻暱鍖呮嫭锛?鈥?鍒涢€犲畨鍏ㄥ拰鏀寔鐨勬儏鎰熸帰绱㈢幆澧?鈥?杩愮敤闊充箰淇冭繘娣卞眰鐨勪釜浜烘礊瀵?鈥?寮曞鎮ㄧ粡鍘咷IM浣撻獙鐨勪笉鍚岄樁娈?
璇烽殢鎰忓垎浜换浣曟兂娉曘€傛偍鎰挎剰鍏堜粙缁嶄竴涓嬭嚜宸卞苟涓斿憡璇夋垜鏄粈涔堝師鍥犺鎮ㄦ潵鍒拌繖閲屽悧锛?""

        welcome_message = welcome_message_zh if is_session_chinese(self) else welcome_message_en
        self.append_chat_message("assistant", welcome_message, phase=GIMState.PRELUDE)

    def ensure_sam_pending_phase(self):
        """Keep the lightweight SAM prompt state synchronized with the session phase."""
        pending_phase = self.sam_state.get("pending_phase")
        if not self.sam_state.get("pre_session_done") and pending_phase is None:
            self.sam_state["pending_phase"] = "pre_session"
            return

    def ensure_panas_pending_phase(self):
        """Keep the lightweight PANAS prompt state synchronized with phase and SAM completion."""
        pending_phase = self.panas_state.get("pending_phase")

        if (
            self.sam_state.get("pre_session_done")
            and not self.panas_state.get("pre_session_done")
            and pending_phase is None
        ):
            self.panas_state["pending_phase"] = "pre_session"

        if (
            self.sam_state.get("post_session_done")
            and not self.panas_state.get("post_session_done")
            and pending_phase is None
        ):
            self.panas_state["pending_phase"] = "post_session"

        phase = self.panas_state.get("pending_phase")
        if phase in ("pre_session", "post_session"):
            if self.panas_state.get("current_order_phase") != phase or not self.panas_state.get("current_order"):
                shuffled_keys = list(PANAS_ITEM_KEYS)
                random.shuffle(shuffled_keys)
                self.panas_state["current_order"] = shuffled_keys
                self.panas_state["current_order_phase"] = phase

    def get_panas_item_label(self, item_key: str, is_chinese: bool) -> str:
        label_key = "zh" if is_chinese else "en"
        return PANAS_ITEM_LABELS.get(item_key, {}).get(label_key, item_key)

    def build_session_results_export(self):
        """Build the unified session export payload."""
        self.session_data["timestamp_start"] = getattr(self, "timestamp_start", self.session_data.get("timestamp_start"))
        self.session_data["user_id"] = self.user_id
        self.session_data["condition"] = self.condition
        self.session_data["condition_order"] = list(self.condition_order or [])
        self.session_data["session_number"] = self.session_number
        prepare_session_metadata(
            self.session_data,
            n_tracks=max(4, len(self.session_data.get("music_sequence", []))),
            focus_theme=self.get_focus_theme_metadata()
        )
        return self.session_data

    def append_ablation_log_once(self):
        if self.ablation_logged:
            return False
        self.build_session_results_export()
        self.session_data["timestamp_end"] = self.session_data.get("timestamp_end") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logged = append_ablation_session_log(
            self.session_data,
            condition_order=self.condition_order,
            session_number=self.session_number,
            positive_items=PANAS_POSITIVE_ITEMS,
            negative_items=PANAS_NEGATIVE_ITEMS,
        )
        if logged:
            self.ablation_logged = True
        return logged

    def get_focus_theme_metadata(self):
        if self.focus_intention and self.focus_intention != "exploring inner experiences":
            return self.focus_intention

        if self.memory and self.memory.memories.get("focus_intentions"):
            latest_focus = self.memory.memories["focus_intentions"][-1]
            if isinstance(latest_focus, dict):
                return latest_focus.get("focus")
            return latest_focus

        return None

    def append_guidance_message_once(self, flag_key: str, message: str):
        """Append a single assistant guidance message once per session transition."""
        if self.ui_message_flags.get(flag_key):
            return False
        self.append_chat_message("assistant", message)
        self.ui_message_flags[flag_key] = True
        return True

    def has_postlude_reflection_response(self):
        """Check whether the user has responded after the post-music reflection prompt."""
        if self.postlude_reflection_prompt_index is None:
            return False
        for msg in self.chat_history[self.postlude_reflection_prompt_index + 1:]:
            if msg.get("role") == "user" and isinstance(msg.get("content"), str) and msg["content"].strip():
                return True
        return False

    def has_postlude_integration_completed(self):
        """Check whether the postlude has had enough natural back-and-forth before final assessment."""
        if self.postlude_reflection_prompt_index is None:
            return False

        messages_after_postlude_start = self.chat_history[self.postlude_reflection_prompt_index + 1:]
        user_turns = 0
        assistant_turns = 0

        for msg in messages_after_postlude_start:
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if msg.get("role") == "user":
                user_turns += 1
            elif msg.get("role") == "assistant":
                assistant_turns += 1

        # Keep the original post-music exploration rhythm by waiting for at least
        # two user turns and two assistant turns in POSTLUDE before showing the
        # final post-session assessments.
        return user_turns >= 2 and assistant_turns >= 2

    def maybe_append_post_session_assessment_intro(self):
        """Preserve the original postlude flow without auto-triggering final assessments."""
        return False

    def get_clean_chat_history_for_model(self):
        """Filter invalid chat turns before sending history back to the model."""
        cleaned_messages = []
        for msg in self.chat_history:
            if not isinstance(msg, dict):
                continue

            role = msg.get("role")
            content = msg.get("content")
            if role not in ("user", "assistant"):
                continue
            if not isinstance(content, str):
                continue

            stripped_content = content.strip()
            if not stripped_content:
                continue
            if stripped_content.startswith("API璇锋眰澶辫触"):
                continue

            cleaned_messages.append({
                "role": role,
                "content": stripped_content
            })

        return cleaned_messages

    def get_system_prompt(self):
        """Get the system prompt based on current state, with memory information."""
        # Get memory information to add to the prompt
        self.ensure_memory_manager()
        memory_prompt = self.memory.get_memory_for_prompt(self.current_state)
            
        # Use the prompt builder to get the full system prompt
        system_prompt = get_full_system_prompt(self.current_state, memory_prompt)
        if self.current_state == GIMState.POSTLUDE:
            system_prompt += SESSION_OVER_PROMPT_INSTRUCTION
        return system_prompt

    def get_music_recommendation_prompts(self):
        """
        Generate a prompt for the LLM to provide music selection criteria.
        This is called when transitioning to the music_imaging phase.
        """
        
        # 鑾峰彇鐒︾偣
        self.ensure_memory_manager()
        self.ensure_music_db()
        if not self.focus_intention:
            # Try to get focus intention from memory
            if self.memory.memories["focus_intentions"]:
                self.focus_intention = self.memory.memories["focus_intentions"][-1]["focus"]
            else:
                self.focus_intention = "exploring inner experiences"
        # 鑾峰彇鎯呯华
        if not self.user_mood:
            if self.memory.memories["emotional_states"]:
                emotions = [state["emotion"] for state in self.memory.memories["emotional_states"][-3:]]
                self.user_mood = "expressing " + ", ".join(emotions)
            else:
                # Try to infer mood from conversation history
                user_inputs = [msg["content"] for msg in self.chat_history if msg["role"] == "user"]
                if user_inputs:
                    last_message = user_inputs[-1]
                    self.user_mood = "expressing " + last_message[:30] + "..."
                else:
                    self.user_mood = "open to exploration"
        
        # Get music preferences from memory
        music_prefs = []
        if self.memory.memories["preferences"]["music"]:
            likes = [pref["genre"] for pref in self.memory.memories["preferences"]["music"] 
                    if pref["sentiment"] == "like"]
            if likes:
                music_prefs.append("likes " + ", ".join(likes))
                
            dislikes = [pref["genre"] for pref in self.memory.memories["preferences"]["music"] 
                       if pref["sentiment"] == "dislike"]
            if dislikes:
                music_prefs.append("dislikes " + ", ".join(dislikes))
                
        user_preferences = "; ".join(music_prefs) if music_prefs else None
        
        # Get mood and genre options
        mood_options = ", ".join(self.music_db.get_attribute_options("mood", MOOD_OPTIONS_NUM))
        genre_options = ", ".join(self.music_db.get_attribute_options("genre", GENRE_OPTIONS_NUM))
        # 鐢熸垚prompt
        system_prompt = get_music_system_prompt(mood_options, genre_options)
        user_prompt = get_music_user_prompt(
            therapy_state=self.current_state,
            user_focus=self.focus_intention,
            user_mood=self.user_mood,
            user_preferences=user_preferences
        )
        return system_prompt, user_prompt

    def classify_state(self, user_message):
        """绗竴姝ワ細鐘舵€佸垎绫?- 浠呰繑鍥炵姸鎬佹爣绛撅紝涓嶇敓鎴愭鏂囧唴瀹?""
        # 浣跨敤prompts涓柊澧炵殑鍙岃鍒嗙被绯荤粺鎻愮ず锛屽熀浜庡綋鍓嶈瑷€鍔ㄦ€侀€夋嫨
        classification_system_prompt = (
            prompts.STATE_CLASSIFICATION_SYSTEM_PROMPT_ZH
            if getattr(prompts, "LANGUAGE", "en") == "zh"
            else prompts.STATE_CLASSIFICATION_SYSTEM_PROMPT_EN
        )

        # 鍑嗗鍒嗙被鐢ㄧ殑娑堟伅
        classification_messages = [
            {
                "role": "system",
                "content": classification_system_prompt
            }
        ]
        
        # 娣诲姞褰撳墠鐘舵€佷綔涓轰笂涓嬫枃
        classification_messages.append({
            "role": "user", 
            "content": f"Current state: {self.current_state}\n\nConversation history: {self.chat_history[-5:] if len(self.chat_history) >= 5 else self.chat_history}\n\nUser's latest input: {user_message}"
        })
        log_message(
            self.session_data,
            "assistant",
            f"State classification requested while in {self.current_state}.",
            "system"
        )
        
        # 鍙戦€佸垎绫昏姹?        conn = http.client.HTTPSConnection("api.openai.com")
        payload = json.dumps({
            "model": MODEL_NAME,
            "max_tokens": 32,  # 鏋佽交閲?            "temperature": 0.1,  # 浣庢俯搴︾‘淇濈ǔ瀹?            "messages": classification_messages
        })
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        # 鍙戦€丄PI璇锋眰
        conn.request("POST", "/v1/chat/completions", payload, headers)
        response = conn.getresponse()
        
        try:
            response_data = json.loads(response.read().decode("utf-8"))
            
            # 鎻愬彇鍒嗙被缁撴灉
            if isinstance(response_data, dict) and 'choices' in response_data:
                classification_text = response_data['choices'][0]['message']['content']
                
                # 鎻愬彇鐘舵€?                state_match = re.search(r'<STATE>(.*?)</STATE>', classification_text)
                if state_match:
                    predicted_state = state_match.group(1)
                    print(f"鐘舵€佸垎绫荤粨鏋滐細{predicted_state}")
                    return predicted_state
                else:
                    print(f"鐘舵€佸垎绫诲け璐ワ紝鏈壘鍒扮姸鎬佹爣绛撅細{classification_text}")
                    return self.current_state  # 淇濇寔褰撳墠鐘舵€?            else:
                print(f"鐘舵€佸垎绫籄PI鍝嶅簲鏍煎紡寮傚父锛歿response_data}")
                return self.current_state
                
        except Exception as e:
            print(f"鐘舵€佸垎绫昏繃绋嬩腑鍙戠敓閿欒锛歿e}")
            return self.current_state

    # def get_next_response(self, user_message):
    #     """鑾峰彇涓嬩竴涓搷搴?- 浣跨敤涓ゆ娴佹按绾匡細鍏堝垽瀹氱姸鎬侊紝鍐嶇敓鎴愬唴瀹?""
    #     global SHOULD_UPDATE_MUSIC_UI
        
    #     # 淇濆瓨鏃х姸鎬?    #     old_state = self.current_state
        
    #     # 绗竴姝ワ細鐘舵€佸垎绫?    #     print("classify state...")
    #     predicted_state = self.classify_state(user_message)
        
    #     # 鏇存柊鐘舵€?    #     self.current_state = predicted_state
    #     print("current state: ", self.current_state)
    #     # 澶勭悊鐘舵€佸彉鍖?    #     if old_state != predicted_state:
    #         print(f"鐘舵€佷粠 {old_state} 鍙樹负 {predicted_state}")
            
    #         # 濡傛灉鐘舵€佷粠闈為煶涔愭垚鍍忓彉涓洪煶涔愭垚鍍忥紝璁剧疆鏍囧織骞堕€夋嫨闊充箰
    #         if old_state != GIMState.MUSIC_IMAGING and predicted_state == GIMState.MUSIC_IMAGING:
    #             print("鐘舵€佸彉涓洪煶涔愭垚鍍忥紝璁剧疆UI鏇存柊鏍囧織骞堕€夋嫨闊充箰")
    #             SHOULD_UPDATE_MUSIC_UI = True
    #             # 绔嬪嵆閫夋嫨闊充箰
    #             if not self.music_selected:
    #                 self.select_music_for_imaging()
        
    #     # 澶勭悊鐢ㄦ埛娑堟伅锛堟洿鏂拌蹇嗭級
    #     self.process_user_message(user_message)
        
    #     # 绗簩姝ワ細鍐呭鐢熸垚 - 浣跨敤鏂扮姸鎬佺殑system prompt
    #     response = self.get_ai_response()
        
    #     # 浠庡搷搴斾腑鎻愬彇鐘舵€佽繘琛屼竴鑷存€ф牎楠?    #     extracted_state = self.extract_state_from_response(response)
    #     if extracted_state != predicted_state:
    #         print(f"璀﹀憡锛氱敓鎴愬唴瀹逛腑鐨勭姸鎬?{extracted_state})涓庡垎绫荤粨鏋?{predicted_state})涓嶄竴鑷?)
    #         # 鍙互閫夋嫨鏇存柊鐘舵€佹垨淇濇寔鍒嗙被缁撴灉
    #         # self.current_state = extracted_state
        
    #     # 杩斿洖鏈€缁堝鐞嗗悗鐨勫搷搴?    #     return self.last_assistant_message
    
    def get_next_response_stream(self, user_message):
        """鑾峰彇涓嬩竴涓狝I娴佸紡鍝嶅簲"""
        # 淇濆瓨鏃х姸鎬?        old_state = self.current_state

        # 绗竴姝ワ細鐘舵€佸垎绫?        print("classify state...")
        predicted_state = self.classify_state(user_message)
        if old_state == GIMState.PRELUDE and not self.has_llm_estimated_va():
            self.extract_va_from_prelude(user_message)

        # 璁板綍褰撳墠 phase 鐨勭敤鎴疯疆鏁帮細杩欐槸 hard cap锛屽彧闄愬埗鏈€澶ц疆娆★紝涓嶉樆姝?LLM 鎻愬墠杩涘叆涓嬩竴闃舵
        if not hasattr(self, "phase_user_turns"):
            self.phase_user_turns = {
                GIMState.PRELUDE: 0,
                GIMState.INDUCTION: 0,
                GIMState.MUSIC_IMAGING: 0,
                GIMState.POSTLUDE: 0,
            }

        self.phase_user_turns[old_state] = self.phase_user_turns.get(old_state, 0) + 1

        # 鏈€澶ц疆鏁颁繚闄╋細濡傛灉 LLM 涓€鐩村仠鐣欏湪 music_imaging锛岀 4 涓敤鎴峰洖澶嶅悗寮哄埗杩涘叆 postlude
        music_imaging_turns = self.phase_user_turns.get(GIMState.MUSIC_IMAGING, 0)
        if old_state == GIMState.MUSIC_IMAGING and music_imaging_turns < MUSIC_IMAGING_MIN_USER_TURNS:
            print("Music imaging minimum exploration not reached; staying in music_imaging.")
            predicted_state = GIMState.MUSIC_IMAGING
        elif old_state == GIMState.MUSIC_IMAGING and music_imaging_turns >= MUSIC_IMAGING_MAX_USER_TURNS:
            print("Music imaging turn limit reached; forcing transition to postlude.")
            predicted_state = GIMState.POSTLUDE
        
        # 鏇存柊鐘舵€?        self.current_state = predicted_state
        print("current state: ", self.current_state)
        
        # 澶勭悊鐘舵€佸彉鍖?        if old_state != predicted_state:
            print(f"鐘舵€佷粠 {old_state} 鍙樹负 {predicted_state}")
            
            # 濡傛灉鐘舵€佷粠闈為煶涔愭垚鍍忓彉涓洪煶涔愭垚鍍忥紝璁剧疆鏍囧織锛堜絾鏆備笉绔嬪嵆閫夋嫨闊充箰锛?            if old_state != GIMState.MUSIC_IMAGING and predicted_state == GIMState.MUSIC_IMAGING:
                print("鐘舵€佸彉涓洪煶涔愭垚鍍忥紝璁剧疆UI鏇存柊鏍囧織")
            elif old_state != GIMState.POSTLUDE and predicted_state == GIMState.POSTLUDE:
                self.postlude_reflection_prompt_index = max(-1, len(self.chat_history) - 2)
        
        # 澶勭悊鐢ㄦ埛娑堟伅锛堟洿鏂拌蹇嗭級
        print("update memory...")
        start_time = time.time()
        self.process_user_message(user_message)
        end_time = time.time()
        print(f"update memory time: {end_time - start_time} seconds")
        
        # 绗簩姝ワ細鍐呭鐢熸垚 - 浣跨敤娴佸紡杈撳嚭
        for chunk in self.get_ai_response_stream():
            yield chunk
        
        # # 浠庡搷搴斾腑鎻愬彇鐘舵€佽繘琛屼竴鑷存€ф牎楠?        # response_text = self.last_assistant_message
        # extracted_state = self.extract_state_from_response(response_text)
        # if extracted_state != predicted_state:
        #     print(f"璀﹀憡锛氱敓鎴愬唴瀹逛腑鐨勭姸鎬?{extracted_state})涓庡垎绫荤粨鏋?{predicted_state})涓嶄竴鑷?)
    
    def update_program_progress(self, stage: str, status: str, progress: float, data: Any = None):
        """鏇存柊Program鏋勫缓杩涘害鏄剧ず
        
        Args:
            stage: 褰撳墠闃舵
            status: 鐘舵€佷俊鎭?            progress: 杩涘害鍊?(0-100)
            data: 鐩稿叧鏁版嵁
        """
        # 鏍规嵁褰撳墠璇█閫夋嫨鏄剧ず鏂囨湰
        is_chinese = is_session_chinese(self)
        
        # 闃舵鍚嶇О鏄犲皠
        stage_names = {
            "analysis": "鍒嗘瀽" if is_chinese else "Analysis",
            "design": "璁捐" if is_chinese else "Design",
            "music_search": "闊充箰妫€绱? if is_chinese else "Music Search",
            "processing": "闊充箰澶勭悊" if is_chinese else "Music Processing",
            "synthesis": "闊抽鍚堟垚" if is_chinese else "Audio Synthesis"
        }
        
        # 鏇存柊杩涘害鐘舵€?        progress_text = f"**{stage_names.get(stage, stage)}**: {status} ({progress:.0f}%)"
        self.progress_status = progress_text
        
        # 鏇存柊闃舵淇℃伅
        if stage == "design" and data:
            if is_chinese:
                phase_text = "### 娌荤枟闃舵璁捐\n\n"
                for i, phase in enumerate(data.get("phases", []), 1):
                    phase_text += f"{i}. **{phase['name']}**\n"
                    phase_text += f"   - 鏃堕暱: {phase['duration']}绉抃n"
                    phase_text += f"   - 鐩殑: {phase['purpose']}\n\n"
            else:
                phase_text = "### Therapy Phases Design\n\n"
                for i, phase in enumerate(data.get("phases", []), 1):
                    phase_text += f"{i}. **{phase['name']}**\n"
                    phase_text += f"   - Duration: {phase['duration']} seconds\n"
                    phase_text += f"   - Purpose: {phase['purpose']}\n\n"
            
            self.phase_info = phase_text
        
        # 鏇存柊闊充箰澶勭悊淇℃伅
        if stage == "processing" and data:
            if is_chinese:
                music_text = "### 闊充箰澶勭悊缁撴灉\n\n"
                for i, seg in enumerate(data.get("segments", []), 1):
                    music_text += f"{i}. **{seg['title']}**\n"
                    music_text += f"   - 鏃堕暱: {seg['duration']}绉抃n"
                    music_text += f"   - 澶勭悊鍙傛暟:\n"
                    music_text += f"     - 閫熷害: {seg['processing']['speed']}x\n"
                    music_text += f"     - 闊宠皟: {seg['processing']['pitch']} 鍗婇煶\n"
                    music_text += f"     - 闊抽噺: {seg['processing']['volume']}x\n"
                    music_text += f"     - 娣″叆: {seg['processing']['fade_in']}ms\n"
                    music_text += f"     - 娣″嚭: {seg['processing']['fade_out']}ms\n\n"
            else:
                music_text = "### Music Processing Results\n\n"
                for i, seg in enumerate(data.get("segments", []), 1):
                    music_text += f"{i}. **{seg['title']}**\n"
                    music_text += f"   - Duration: {seg['duration']} seconds\n"
                    music_text += f"   - Processing:\n"
                    music_text += f"     - Speed: {seg['processing']['speed']}x\n"
                    music_text += f"     - Pitch: {seg['processing']['pitch']} semitones\n"
                    music_text += f"     - Volume: {seg['processing']['volume']}x\n"
                    music_text += f"     - Fade in: {seg['processing']['fade_in']}ms\n"
                    music_text += f"     - Fade out: {seg['processing']['fade_out']}ms\n\n"
            
            self.music_info = music_text

    def select_music_for_imaging(self, progress_callback=None):
        """浣跨敤GIM闊抽缂栬緫浠ｇ悊鏋勫缓鍜屾覆鏌撻煶涔怭rogram"""
        # 濡傛灉宸茬粡閫夋嫨杩囬煶涔愶紝鍒欎笉鍐嶉噸澶嶉€夋嫨
        if self.music_selected:
            print("Music already selected for this session, skipping selection")
            return

        if self.condition == "baseline":
            print("Selecting music through baseline retrieval path...")
            try:
                self.ensure_music_db()
                system_prompt, user_prompt = self.get_music_recommendation_prompts()
                criteria = self.music_db.get_music_criteria_json(system_prompt, user_prompt, self.api_key)
                baseline_track = retrieve_baseline_track(
                    self.music_db,
                    criteria,
                    track_index=0,
                    used_track_ids=self.baseline_used_track_ids,
                    music_root="../toy_dataset/mp3",
                )
                self.baseline_used_track_ids.add(baseline_track.get("track_id"))
                self.selected_music_tracks = [baseline_track]
                self.append_music_tracks_to_session_data()
                self.music_selected = True
                print(f"Baseline music selection completed - {baseline_track.get('track_id')}")
            except Exception as e:
                print(f"Baseline music selection failed: {e}")
                import traceback
                traceback.print_exc()
                self.music_selected = False
            return

        self.ensure_audio_agent()
            
        print("寮€濮嬩娇鐢℅IM闊抽缂栬緫浠ｇ悊鏋勫缓闊充箰Program...")
        
        try:
            # 浣跨敤浼犲叆鐨勮繘搴﹀洖璋冩垨榛樿鐨勮繘搴﹀洖璋?            callback = progress_callback or self.update_program_progress
            # 浣跨敤GIM闊抽缂栬緫浠ｇ悊浠庡璇濆巻鍙叉瀯寤哄拰娓叉煋Program
            self.gim_program_result = self.audio_agent.build_and_render_program(
                self.chat_history,
                progress_callback=callback
            )
            
            print("GIM Program鏋勫缓瀹屾垚锛?)
            print(f"鍒嗘瀽缁撴灉: {self.gim_program_result['analysis']}")
            print(f"Program闃舵鏁? {len(self.gim_program_result['program'])}")
            print(f"杈撳嚭鏂囦欢: {self.gim_program_result['output']['file']}")
            
            # 涓轰簡鍏煎鐜版湁UI绯荤粺锛屽皢鍚堟垚鐨勯煶棰戜綔涓?selected_music_tracks"
            # 杩欐牱UI鍙互灞曠ず鏈€缁堢殑Program娣烽煶
            output_file = self.gim_program_result['output']['file']
            if self.condition == "kimusic":
                prepare_session_metadata(self.session_data, n_tracks=max(4, NUM_MUSIC_TRACKS))
                waypoint_sequence = self.session_data.get("waypoint_sequence") or []
                waypoint_va = (
                    waypoint_sequence[-1]
                    if waypoint_sequence
                    else self.session_data.get("target_state_va") or self.session_data.get("current_state_va")
                )
                proxy_track = create_proxy_generated_track(
                    waypoint_va=waypoint_va,
                    session_id=self.session_data.get("session_id"),
                    track_index=0,
                    fallback_audio_path=output_file,
                    waypoint_sequence=waypoint_sequence
                )
                proxy_track["duration_seconds"] = self.gim_program_result['output']['total_seconds']
                self.render_kimusic_track_once(proxy_track, output_file)
                self.selected_music_tracks = [proxy_track]
            else:
                self.selected_music_tracks = [{
                    'filename': os.path.basename(output_file),
                    'title': 'GIM Program Mix - Complete Therapy Session',
                    'full_path': output_file,  # 淇濆瓨瀹屾暣璺緞
                    'duration_seconds': self.gim_program_result['output']['total_seconds']
                }]
            self.append_music_tracks_to_session_data()
            
            self.music_selected = True
            print(f"GIM Program闊抽鍚堟垚瀹屾垚 - 杈撳嚭鏂囦欢: {output_file}")
            
        except Exception as e:
            print(f"GIM Program鏋勫缓澶辫触: {e}")
            import traceback
            traceback.print_exc()
            self.music_selected = False
            return

            # 濡傛灉GIM浠ｇ悊澶辫触锛屽洖閫€鍒板師濮嬬殑闊充箰閫夋嫨鏂规硶
            print("鍥為€€鍒板師濮嬮煶涔愰€夋嫨鏂规硶...")
            system_prompt, user_prompt = self.get_music_recommendation_prompts()
            criteria = self.music_db.get_music_criteria_json(system_prompt, user_prompt, self.api_key)
            self.selected_music_tracks = self.music_db.retrieve_music_for_therapy(criteria, num_tracks=NUM_MUSIC_TRACKS)
            self.append_music_tracks_to_session_data()
            self.music_selected = True
            print(f"鍥為€€闊充箰閫夋嫨瀹屾垚 - {len(self.selected_music_tracks)} tracks selected")

    def process_user_message(self, user_message):
        """澶勭悊鐢ㄦ埛娑堟伅锛屾洿鏂拌蹇?""
        self.ensure_memory_manager()
        if self.last_assistant_message:
            self.memory.process_message(user_message, self.last_assistant_message, api_key=self.api_key)
        else:
            self.memory.process_message(user_message, api_key=self.api_key)
        
        # 鏇存柊鑱婂ぉ鍘嗗彶
        # self.chat_history.append({"role": "user", "content": user_message}) # 鍒犻櫎杩欒閲嶅鐨勫巻鍙茶褰曟坊鍔?            
    def get_ai_response(self):
        """鑾峰彇AI鍝嶅簲 - 涓嶅啀闇€瑕佺敓鎴愮姸鎬佹爣绛撅紝鍥犱负鐘舵€佸垎绫诲凡鐙珛瀹屾垚"""
        # 鍒涘缓瀵硅瘽娑堟伅
        messages = [
            {
                "role": "system",
                "content": self.get_system_prompt()
            }
        ]
        
        # 妫€鏌ユ槸鍚﹂渶瑕佹€荤粨瀵硅瘽
        self.ensure_memory_manager()
        if len(self.chat_history) >= self.summarization_threshold:
            print("!!!!!Conversation needs summarization!!!!!!!!!********")
            summary = self.memory.summarize_conversation(self.chat_history, api_key=self.api_key)
            if summary:
                print(f"Conversation summarized: {len(self.chat_history)} messages processed")
                # 濡傛灉瀵硅瘽寰堥暱锛屽彲浠ラ€夋嫨鎴柇鑱婂ぉ鍘嗗彶浠ヨ妭鐪丄PI璋冪敤涓殑浠ょ墝
                if len(self.chat_history) > 30:  # 濡傛灉瀵硅瘽寰堥暱
                    # 淇濈暀鍓嶅嚑鏉℃秷鎭綔涓轰笂涓嬫枃鍜屾渶杩戠殑娑堟伅
                    self.chat_history = self.chat_history[:5] + self.chat_history[-15:]
                    print(f"Chat history truncated to {len(self.chat_history)} messages")
        
        # 娣诲姞娓呮礂鍚庣殑鑱婂ぉ鍘嗗彶浣滀负涓婁笅鏂?        for msg in self.get_clean_chat_history_for_model():
            messages.append(msg)

        print("!!!!!Cleaned messages before LLM call:")
        print(messages)
        print("########################################################")
        
        # 鍑嗗API璇锋眰
        conn = http.client.HTTPSConnection("api.openai.com")
        payload = json.dumps({
            "model": MODEL_NAME,
            "max_tokens": MAX_TOKENS_RESPONSE,
            "messages": messages
        })
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        # 鍙戦€丄PI璇锋眰
        conn.request("POST", "/v1/chat/completions", payload, headers)
        response = conn.getresponse()
        try:
            response_data = json.loads(response.read().decode("utf-8"))
        except json.decoder.JSONDecodeError as e:
            print(f"JSON瑙ｆ瀽閿欒: {e}")
            response_text = "寰堟姳姝夛紝AI杩斿洖鍐呭瑙ｆ瀽澶辫触锛岃绋嶅悗閲嶈瘯銆?
            self.last_assistant_message = response_text
            return response_text
        except Exception as e:
            print(f"鑾峰彇AI鍝嶅簲鏃跺彂鐢熸湭鐭ラ敊璇? {e}")
            response_text = "寰堟姳姝夛紝AI鏈嶅姟鏆傛椂涓嶅彲鐢紝璇风◢鍚庨噸璇曘€?
            self.last_assistant_message = response_text
            return response_text
        
        print("--------------------------------")
        print(response_data)
        
        # 浠嶢PI鍝嶅簲涓彁鍙栧搷搴旀枃鏈?        if isinstance(response_data, dict):
            if 'choices' in response_data:
                response_text = response_data['choices'][0]['message']['content']
            elif 'content' in response_data:
                response_text = response_data['content'][0]['text']
            else:
                print("Unexpected response format:", response_data)
                response_text = "I apologize, but I encountered an issue processing the response. Could you please try again?"
        else:
            print("Unexpected response type:", type(response_data))
            response_text = "I apologize, but I encountered an issue processing the response. Could you please try again?"
            
        print("Response text:", response_text)
        
        # 鏆傛椂瀛樺偍鍔╂墜鐨勫搷搴斾互渚涜蹇嗗鐞?        self.last_assistant_message = response_text
        
        # 鏆傛椂鏇存柊鑱婂ぉ鍘嗗彶 - 娉ㄦ剰锛歟xtract_state_from_response鍙兘浼氫慨鏀硅繖涓唴瀹?        self.append_chat_message("assistant", response_text)
        
        return response_text

    def get_ai_response_stream(self):
        """鑾峰彇AI娴佸紡鍝嶅簲"""
        # 鍒涘缓瀵硅瘽娑堟伅
        messages = [
            {
                "role": "system",
                "content": self.get_system_prompt()
            }
        ]
        
        # 妫€鏌ユ槸鍚﹂渶瑕佹€荤粨瀵硅瘽
        self.ensure_memory_manager()
        if len(self.chat_history) >= self.summarization_threshold:
            print("!!!!!Conversation needs summarization!!!!!!!!!********")
            summary = self.memory.summarize_conversation(self.chat_history, api_key=self.api_key)
            if summary:
                print(f"Conversation summarized: {len(self.chat_history)} messages processed")
                # 濡傛灉瀵硅瘽寰堥暱锛屽彲浠ラ€夋嫨鎴柇鑱婂ぉ鍘嗗彶浠ヨ妭鐪丄PI璋冪敤涓殑浠ょ墝
                if len(self.chat_history) > 30:  # 濡傛灉瀵硅瘽寰堥暱
                    # 淇濈暀鍓嶅嚑鏉℃秷鎭綔涓轰笂涓嬫枃鍜屾渶杩戠殑娑堟伅
                    self.chat_history = self.chat_history[:5] + self.chat_history[-15:]
                    print(f"Chat history truncated to {len(self.chat_history)} messages")
        
        # 娣诲姞鑱婂ぉ鍘嗗彶浣滀负涓婁笅鏂?        for msg in self.get_clean_chat_history_for_model():
            messages.append(msg)

        print("!!!!!Cleaned messages before LLM call:")
        print(messages)
        print("########################################################")
        
        # 鍑嗗API璇锋眰 - 浣跨敤娴佸紡杈撳嚭
        payload = json.dumps({
            "model": MODEL_NAME,
            "max_tokens": MAX_TOKENS_RESPONSE,
            "messages": messages,
            "stream": True  # 鍚敤娴佸紡杈撳嚭
        })
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Connection': 'close'
        }
        
        # 鍙戦€佹祦寮廇PI璇锋眰
        try:
            conn = http.client.HTTPSConnection("api.openai.com", timeout=None)
            conn.request("POST", "/v1/chat/completions", payload, headers)
            response = conn.getresponse()
            
            if response.status == 200:
                full_response = ""
                pending_visible_text = ""
                token_detected = False
                token_tail_length = max(0, len(SESSION_OVER_TOKEN) - 1)
                for line in response:
                    line = line.decode('utf-8').strip()
                    if line.startswith('data: '):
                        data = line[6:]  # 绉婚櫎 'data: ' 鍓嶇紑
                        if data == '[DONE]':
                            break
                        try:
                            json_data = json.loads(data)
                            if 'choices' in json_data and len(json_data['choices']) > 0:
                                delta = json_data['choices'][0].get('delta', {})
                                if 'content' in delta:
                                    content = delta['content']
                                    full_response += content
                                    if token_detected:
                                        visible_text = content.replace(SESSION_OVER_TOKEN, "")
                                        if visible_text:
                                            yield visible_text
                                        continue
                                    pending_visible_text += content
                                    if SESSION_OVER_TOKEN in pending_visible_text:
                                        visible_text, _, after_token_text = pending_visible_text.partition(SESSION_OVER_TOKEN)
                                        if visible_text:
                                            yield visible_text
                                        if after_token_text:
                                            yield after_token_text
                                        pending_visible_text = ""
                                        token_detected = True
                                        continue
                                    if len(pending_visible_text) > token_tail_length:
                                        visible_text = pending_visible_text[:-token_tail_length]
                                        pending_visible_text = pending_visible_text[-token_tail_length:]
                                        if visible_text:
                                            yield visible_text
                        except json.JSONDecodeError:
                            continue
                if not token_detected and pending_visible_text:
                    yield pending_visible_text
                
                # 淇濆瓨瀹屾暣鍝嶅簲
                cleaned_response = self.strip_session_over_token(full_response)
                self.last_assistant_message = cleaned_response
                if cleaned_response.strip():
                    self.append_chat_message("assistant", cleaned_response)
                should_end_from_token = token_detected and not self.response_still_invites_continuation(cleaned_response)
                should_end_from_cap = (
                    self.current_state == GIMState.POSTLUDE
                    and self.phase_user_turns.get(GIMState.POSTLUDE, 0) >= POSTLUDE_MAX_USER_TURNS
                )
                if should_end_from_token or should_end_from_cap:
                    self.mark_ready_for_post_session_assessment()
                
            else:
                error_text = f"API璇锋眰澶辫触: {response.status}"
                self.last_assistant_message = error_text
                yield error_text
                
        except Exception as e:
            error_text = f"娴佸紡璇锋眰澶辫触: {str(e)}"
            self.last_assistant_message = error_text
            yield error_text
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # def extract_state_from_response(self, response_text):
    #     """浠庡搷搴斾腑鎻愬彇鐘舵€?""
    #     state_match = re.search(r'<STATE>(.*?)</STATE>', response_text)
    #     if state_match:
    #         new_state = state_match.group(1)
            
    #         # 浠庡搷搴斾腑鍒犻櫎鐘舵€佹爣绛?    #         clean_response = re.sub(r'<STATE>.*?</STATE>\s*', '', response_text).strip()
            
    #         # 濡傛灉杩涘叆闊充箰鎴愬儚闃舵涓斿凡閫夋嫨闊充箰锛屾坊鍔犻煶涔愭彁绀轰俊鎭?    #         if new_state == GIMState.MUSIC_IMAGING and self.music_selected and self.selected_music_tracks:
    #             # 娣诲姞闊充箰鎻愮ず鍒板搷搴斾腑 - 浣跨敤褰撳墠璇█鐨勬彁绀?    #             # 纭繚瀵煎叆鏈€鏂扮殑MUSIC_NOTE鍙橀噺锛屽畠浼氶殢璇█鍒囨崲鑰屽彉鍖?    #             from prompts import MUSIC_NOTE
                
    #             # # 濡傛灉鏈塆IM Program缁撴灉锛屾坊鍔犻澶栫殑Program淇℃伅
    #             # if self.gim_program_result:
    #             #     program_info = f"\n\n**鎮ㄧ殑GIM闊充箰娌荤枟Program宸插噯澶囧氨缁?*\n"
    #             #     program_info += f"- 娌荤枟鐩爣: {self.gim_program_result['analysis'].get('therapeutic_goal', '鎯呮劅鎺㈢储')}\n"
    #             #     program_info += f"- 褰撳墠鎯呯华: {self.gim_program_result['analysis'].get('current_emotion', '寰呮帰绱?)}\n"
    #             #     program_info += f"- Program闃舵: {len(self.gim_program_result['program'])}涓樁娈礬n"
    #             #     program_info += f"- 鎬绘椂闀? {self.gim_program_result['output']['total_seconds']}绉抃n"
    #             #     clean_response += program_info
                
    #             clean_response += MUSIC_NOTE
                
    #             print("宸叉坊鍔犻煶涔愭彁绀轰俊鎭埌鍝嶅簲涓?)
            
    #         # 鏇存柊鍝嶅簲鏂囨湰
    #         self.last_assistant_message = clean_response
            
    #         # 鏇存柊鑱婂ぉ鍘嗗彶涓殑鏈€鍚庝竴鏉℃秷鎭?    #         if self.chat_history and self.chat_history[-1]["role"] == "assistant":
    #             self.chat_history[-1]["content"] = clean_response
            
    #         return new_state
        
    #     # 濡傛灉娌℃湁鎵惧埌鐘舵€佹爣绛撅紝淇濇寔褰撳墠鐘舵€?    #     return self.current_state



# 纭繚杈撳嚭鐩綍瀛樺湪
os.makedirs("output", exist_ok=True)

def get_music_tracks(session: GIMTherapySession):
    """Get the currently selected music tracks for display"""
    music_files = []
    music_titles = []
    
    # Always check for selected music tracks, regardless of state
    if session.selected_music_tracks:
        print(f"Found {len(session.selected_music_tracks)} selected tracks")
        
        for track in session.selected_music_tracks:
            if "filename" in track:
                filename = track["filename"]
                title = track.get("title", filename)
                
                # 棣栧厛妫€鏌ユ槸鍚︽湁full_path锛圙IM Program鍚堟垚鐨勬枃浠讹級
                if "full_path" in track and os.path.exists(track["full_path"]) and os.path.isfile(track["full_path"]):
                    print(f"Found GIM Program file: {track['full_path']}")
                    music_files.append(track["full_path"])
                    music_titles.append(title)
                    continue
                
                # 妫€鏌ョ浉瀵硅矾寰勫拰缁濆璺緞
                if os.path.isabs(filename) and os.path.exists(filename) and os.path.isfile(filename):
                    print(f"Found music file (absolute path): {filename}")
                    music_files.append(filename)
                    music_titles.append(title)
                else:
                    # Check if file exists in the correct music directory
                    file_path = os.path.join("..", "toy_dataset", "mp3", filename)
                    print(f"Looking for music file: {file_path}")
                    
                    if os.path.exists(file_path) and os.path.isfile(file_path):
                        print(f"Found music file: {file_path}")
                        # 鐩存帴浣跨敤鏂囦欢璺緞锛孏radio浼氬鐞嗘枃浠朵紶杈?                        music_files.append(file_path)
                        music_titles.append(title)
                    else:
                        print(f"WARNING: Music file not found: {file_path}")
                        # Try fallback locations
                        alt_path = os.path.join("toy_dataset", filename)
                        if os.path.exists(alt_path) and os.path.isfile(alt_path):
                            print(f"Found music file in alternate location: {alt_path}")
                            music_files.append(alt_path)
                            music_titles.append(title)
    
    print(f"Returning {len(music_files)} music files and titles")
    return music_files, music_titles


def export_session_results(session: GIMTherapySession):
    """Persist the unified session results to disk."""
    session_results = session.build_session_results_export()
    export_path = save_session_json(session_results, output_dir="session_results")
    print(f"SESSION RESULTS SAVED: {export_path}")
    return export_path

# def get_gim_program_info():
#     """鑾峰彇GIM Program鐨勮缁嗕俊鎭敤浜庢樉绀?(鏆傛椂涓嶅睍绀簆rograminfo)"""
#     if not therapy_session.gim_program_result:
#         return ""
    
#     result = therapy_session.gim_program_result
#     info = "## GIM闊充箰娌荤枟Program璇︽儏\n\n"
    
#     # 鍒嗘瀽缁撴灉
#     analysis = result.get('analysis', {})
#     info += f"**褰撳墠鎯呯华鐘舵€?** {analysis.get('current_emotion', '鏈煡')}\n"
#     info += f"**鏍稿績涓婚:** {', '.join(analysis.get('key_themes', []))}\n"
#     info += f"**娌荤枟鐩爣:** {analysis.get('therapeutic_goal', '鏈瀹?)}\n\n"
    
#     # Program闃舵
#     info += "**Program闃舵璁捐:**\n"
#     for i, phase in enumerate(result.get('program', []), 1):
#         info += f"{i}. **{phase.get('phase', f'闃舵{i}')}** ({phase.get('duration_seconds', 0)}绉?\n"
#         info += f"   - 鐩殑: {phase.get('purpose', '鏈鏄?)}\n"
#         criteria = phase.get('search_criteria', {})
#         if criteria.get('mood_keywords'):
#             info += f"   - 鎯呯华鍏抽敭璇? {', '.join(criteria.get('mood_keywords', []))}\n"
#         info += f"   - 鑺傚鍋忓ソ: {criteria.get('tempo_preference', '鏈瀹?)}\n"
#         info += f"   - 鍔ㄦ€佸亸濂? {criteria.get('dynamics_preference', '鏈瀹?)}\n\n"
    
#     # 杈撳嚭淇℃伅
#     output_info = result.get('output', {})
#     info += f"**鍚堟垚缁撴灉:**\n"
#     info += f"- 杈撳嚭鏂囦欢: {os.path.basename(output_info.get('file', ''))}\n"
#     info += f"- 鎬绘椂闀? {output_info.get('total_seconds', 0)}绉抃n"
    
#     return info

# Create the Gradio interface with state management
with gr.Blocks(
    title="GIM Therapy Session",
    theme=gr.themes.Soft(),
    js="""() => {
        if (window.__gimExitWarningInstalled) {
            return;
        }
        window.__gimExitWarningInstalled = true;
        window.__gimExperimentComplete = false;
        window.__gimMusicPlaying = false;
        const getSelectedLanguage = () => {
            const checked = Array.from(document.querySelectorAll('input[type="radio"]:checked'))
                .map((input) => input.value || input.getAttribute("aria-label") || input.parentElement?.innerText || "")
                .join(" ");
            return checked.includes("English") ? "en" : "zh";
        };
        const getExitWarning = () => (
            getSelectedLanguage() === "en"
                ? "The experiment is not complete yet.\\nAre you sure you want to leave?\\nUnsaved data may be lost."
                : "瀹為獙灏氭湭瀹屾垚锛岀‘瀹氳閫€鍑哄悧锛焅\n鏈畬鎴愮殑鏁版嵁鍙兘鏃犳硶淇濆瓨銆?
        );
        const setChatLocked = (locked) => {
            const chatInput = document.querySelector("#gim-chat-input textarea");
            const submitButton = document.querySelector("#gim-submit-btn button");
            [chatInput, submitButton].forEach((element) => {
                if (!element) {
                    return;
                }
                element.disabled = locked;
                element.style.pointerEvents = locked ? "none" : "";
                element.style.opacity = locked ? "0.45" : "";
            });
        };
        const wireAudio = () => {
            document.querySelectorAll("audio").forEach((audio) => {
                if (audio.dataset.gimPlaybackWired) {
                    return;
                }
                audio.dataset.gimPlaybackWired = "1";
                audio.addEventListener("play", () => {
                    window.__gimMusicPlaying = true;
                    setChatLocked(true);
                });
                audio.addEventListener("ended", () => {
                    window.__gimMusicPlaying = false;
                    setChatLocked(false);
                });
            });
        };
        const observer = new MutationObserver(() => wireAudio());
        observer.observe(document.body, { childList: true, subtree: true });
        wireAudio();
        window.addEventListener("beforeunload", (event) => {
            if (document.querySelector(".final-completion-card")) {
                window.__gimExperimentComplete = true;
            }
            if (window.__gimExperimentComplete) {
                return;
            }
            const warning = getExitWarning();
            event.preventDefault();
            event.returnValue = warning;
            return warning;
        });
    }""",
    css="""
        .left-drawer {
            transition: all 0.3s ease-in-out;
        }
        .right-drawer {
            transition: all 0.3s ease-in-out;
        }
        .main-content {
            transition: all 0.3s ease-in-out;
        }
        .drawer-content {
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
            margin: 10px 0;
        }
        .progress-indicator {
            background: linear-gradient(90deg, #007bff, #0056b3);
            color: white;
            padding: 10px;
            border-radius: 5px;
            margin: 5px 0;
        }
        .memory-item {
            background: #e9ecef;
            padding: 8px;
            border-radius: 5px;
            margin: 5px 0;
            border-left: 4px solid #007bff;
        }
        .program-phase {
            background: #f0f8ff;
            padding: 10px;
            border-radius: 5px;
            margin: 8px 0;
            border-left: 4px solid #28a745;
        }
        .final-completion-card {
            padding: 24px;
            border: 1px solid #d0d7de;
            border-radius: 6px;
            background: #ffffff;
            color: #1f2328;
            line-height: 1.55;
        }
        .final-completion-card h2,
        .final-completion-card p {
            color: #1f2328;
        }
        .gradio-container .loading,
        .gradio-container .generating,
        .gradio-container .wrap.default,
        .gradio-container .wrap.pending {
            color: #1f2328 !important;
        }
        @media (max-width: 640px) {
            .final-completion-card {
                background: #ffffff;
                color: #111827;
                border-color: #c9d1d9;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            }
            .gradio-container .loading,
            .gradio-container .generating,
            .gradio-container .wrap.default,
            .gradio-container .wrap.pending {
                background: rgba(255,255,255,0.96) !important;
                color: #111827 !important;
                border-radius: 8px;
            }
        }
    """
) as demo:
    # 浼氳瘽鐘舵€佺鐞?    session_state = gr.State(None)
    washout_timer = gr.Timer(1, active=False) if hasattr(gr, "Timer") else None
    
    # 娣诲姞璇█鍒囨崲
    current_language = gr.State(value="en")
    
    # 鎶藉眽鐘舵€?    left_drawer_visible = gr.State(False)
    right_drawer_visible = gr.State(False)
    
    with gr.Column() as study_entry:
        with gr.Row():
            title_en = gr.Markdown("# Guided Imagery and Music (GIM) Therapy Session")
            title_zh = gr.Markdown("# 寮曞寮忛煶涔愪笌鎰忚薄 (GIM) 娌荤枟浼氳瘽", visible=False)
        
        with gr.Row():
            intro_en = gr.Markdown("""Welcome to your virtual GIM therapy session. This space is designed to guide you through a therapeutic journey 
        combining music and imagery. Feel free to share your thoughts and feelings openly.""")
            intro_zh = gr.Markdown("""娆㈣繋鎮ㄥ弬鍔犵殑铏氭嫙GIM娌荤枟浼氳瘽銆傝繖涓┖闂存棬鍦ㄥ紩瀵兼偍閫氳繃缁撳悎闊充箰鍜屾剰璞＄殑娌荤枟鏃呯▼銆?            璇烽殢鎰忓紑鏀惧湴鍒嗕韩鎮ㄧ殑鎯虫硶鍜屾劅鍙椼€?"", visible=False)

        study_instructions_en = gr.Markdown("""# Study Instructions

Welcome and thank you for participating in this study!

Please follow the steps below to complete the experiment:

1. Record the automatically generated **Participant ID**.
2. Complete the background questionnaire provided by the researcher (enter your Participant ID in the questionnaire).
3. Return to this page and click **Start Session**.
4. Follow the on-screen instructions to complete the pre-session questionnaires, therapeutic conversation, music experience, and post-music discussion.
5. After the chatbot completes the session, the system will **automatically** proceed to the post-session questionnaires.
6. After Session 1, please take a **5-minute break** as instructed by the system.
7. When the countdown finishes, click **Start Session 2** to begin the second session.
8. Session 2 follows the same procedure as Session 1.
9. After completing all questionnaires, the page will display **"Experiment Completed"**, indicating that the study has finished.

---

## Notes

- We recommend wearing headphones for the best experience.
- We recommend using a desktop or laptop computer with **Google Chrome** or **Microsoft Edge**.
- Please keep your internet connection stable. Do **not** refresh the page, close the browser window, or navigate away from the page during the experiment.
- Please keep your **Participant ID** in a safe place, as it is required for the background questionnaire.
- Music generation or loading may take several seconds. Please be patient. If necessary, you may click **Program** to check the generation progress.
- After the music finishes, the system will continue with a brief conversation. Please continue following the chatbot's guidance.
- When the system automatically proceeds to the post-session questionnaires, simply complete them as instructed. **There is no need to click "End Conversation."**
- During the experiment, you will mainly use the following buttons:
  - **Start Session**
  - **Submit Questionnaire**
  - **Send**
  - **Play Music**
  - **Start Session 2**
- There are no right or wrong answers. Please respond according to your genuine thoughts and feelings.
""")
        study_instructions_zh = gr.Markdown("""# 瀹為獙璇存槑

娆㈣繋鍙傚姞鏈爺绌讹紒

璇锋寜鐓т互涓嬫楠ゅ畬鎴愬疄楠岋細

1. 璁板綍绯荤粺鑷姩鐢熸垚鐨?Participant ID銆?2. 瀹屾垚鐮旂┒浜哄憳鎻愪緵鐨勮儗鏅棶鍗凤紙濉啓 Participant ID锛夈€?3. 杩斿洖鏈〉闈紝鐐瑰嚮銆愬紑濮嬩細璇濄€戙€?4. 鏍规嵁椤甸潰鎻愮ず瀹屾垚浼氬墠閲忚〃銆佹不鐤楀璇濄€侀煶涔愪綋楠屼互鍙婇煶涔愮粨鏉熷悗鐨勪氦娴佺幆鑺傘€?5. 褰撹亰澶╂満鍣ㄤ汉瀹屾垚鏈浼氳瘽鍚庯紝绯荤粺灏嗚嚜鍔ㄨ繘鍏ヤ細鍚庨噺琛ㄣ€?6. 绗竴杞粨鏉熷悗锛岃鎸夌収绯荤粺鎻愮ず浼戞伅 5 鍒嗛挓銆?7. 鍊掕鏃剁粨鏉熷悗锛岀偣鍑汇€愬紑濮嬬浜屾浼氳瘽銆戝畬鎴愮浜岃疆浣撻獙銆?8. 绗簩杞祦绋嬩笌绗竴杞浉鍚屻€?9. 瀹屾垚鎵€鏈夐噺琛ㄥ悗锛岄〉闈㈡樉绀衡€滃疄楠屽畬鎴愨€濆嵆琛ㄧず瀹為獙缁撴潫銆?
娉ㄦ剰浜嬮」

- 寤鸿浣╂埓鑰虫満瀹屾垚瀹為獙銆?- 寤鸿浣跨敤鐢佃剳绔紙Chrome 鎴?Edge 娴忚鍣級銆?- 瀹為獙杩囩▼涓淇濇寔缃戠粶绋冲畾锛屼笉瑕佸埛鏂伴〉闈€佸叧闂祻瑙堝櫒鎴栬繑鍥炰笂涓€椤点€?- 璇峰Ε鍠勪繚瀛?Participant ID锛屼互渚垮畬鎴愯儗鏅棶鍗枫€?- 闊充箰鐢熸垚鎴栧姞杞藉彲鑳介渶瑕佸嚑鍗佺锛岃鑰愬績绛夊緟銆傚绛夊緟鏃堕棿杈冮暱锛屽彲鐐瑰嚮銆愮▼搴忋€戞煡鐪嬬敓鎴愯繘搴︺€?- 闊充箰鎾斁缁撴潫鍚庯紝绯荤粺浠嶄細缁х画杩涜涓€娈典氦娴侊紝璇锋牴鎹彁绀虹户缁畬鎴愭暣涓細璇濄€?- 褰撶郴缁熻嚜鍔ㄨ繘鍏ヤ細鍚庨噺琛ㄦ椂锛岃鐩存帴瀹屾垚閲忚〃濉啓锛屾棤闇€鎵嬪姩鐐瑰嚮銆愮粨鏉熷璇濄€戙€?- 闄ゃ€愬紑濮嬩細璇濄€戙€愭彁浜ら噺琛ㄣ€戙€愬彂閫併€戙€愰煶涔愭挱鏀俱€戙€愬紑濮嬬浜屾浼氳瘽銆戝锛屽叾浣欐寜閽竴鑸棤闇€浣跨敤銆?- 鎵€鏈夐棶棰樺潎鏃犳爣鍑嗙瓟妗堬紝璇锋牴鎹嚜宸辩殑鐪熷疄鎰熷彈浣滅瓟銆?""", visible=False)
        
        # 璇█鍒囨崲寮€鍏?        with gr.Row():
            language_radio = gr.Radio(
                ["English", "涓枃"], 
                label="Language / 璇█", 
                value="English",
                interactive=True
            )

        questionnaire_instruction_en = gr.Markdown("Questionnaire: please follow the study questionnaire link provided by the researcher, then click Start Experiment when you are ready.")
        questionnaire_instruction_zh = gr.Markdown("闂嵎锛氳鎸夌収鐮旂┒鑰呮彁渚涚殑闂嵎閾炬帴瀹屾垚濉啓锛屽噯澶囧ソ鍚庣偣鍑诲紑濮嬪疄楠屻€?, visible=False)

        with gr.Row(elem_id="participant-controls") as participant_controls:
            initial_texts = get_ui_texts(False)
            user_id_input = gr.Textbox(
                label=initial_texts["participant_id_label"],
                placeholder=initial_texts["participant_id_placeholder"],
                value="",
                show_copy_button=True,
                interactive=True
            )
            start_session_btn = gr.Button(initial_texts["start_session"], variant="secondary")
        participant_status = gr.Markdown("")
    washout_display = gr.HTML("", visible=False)
    final_completion_display = gr.HTML("", visible=False)
    
    # 鍒涘缓涓夋爮鎶藉眽寮忓竷灞€
    with gr.Row(visible=False) as therapy_workspace:
        # 宸︿晶鎶藉眽 - 鐢ㄦ埛璁板繂
        with gr.Column(scale=1, visible=False, elem_classes="left-drawer") as left_drawer:
            with gr.Column(elem_classes="drawer-content"):
                gr.Markdown("### 馃懁 User Memory / 鐢ㄦ埛璁板繂")
                user_memory_display = gr.Markdown("Memory content will be displayed here.\n鐢ㄦ埛璁板繂鍐呭灏嗗湪姝ゆ樉绀恒€?)
        
        # 涓棿涓诲尯鍩?        with gr.Column(scale=3, elem_classes="main-content") as main_content:
            # 鍒涘缓鑱婂ぉ鐣岄潰
            chatbot = gr.Chatbot(
                height=600,
                show_label=False,
                container=True,
                type="messages",
                value=[]
            )
            audio_player = gr.Audio(
                label=initial_texts["audio_label"],
                type="filepath",
                visible=False,
                interactive=False
            )

            with gr.Column(visible=False) as sam_panel:
                sam_title = gr.Markdown("### SAM Rating")
                sam_instruction = gr.Markdown("")
                sam_valence = gr.Slider(minimum=1, maximum=9, step=1, value=5, label="Valence", info="How pleasant or unpleasant do you feel right now?")
                sam_arousal = gr.Slider(minimum=1, maximum=9, step=1, value=5, label="Arousal", info="How calm or excited do you feel right now?")
                sam_submit_btn = gr.Button("Submit SAM", variant="secondary")
                sam_status = gr.Markdown("", visible=False)

            with gr.Column(visible=False) as panas_panel:
                panas_title = gr.Markdown("### PANAS Rating")
                panas_item_components = []
                with gr.Row():
                    with gr.Column():
                        for idx in range(10):
                            panas_item_components.append(
                                gr.Radio(
                                    choices=[1, 2, 3, 4, 5],
                                    value=3,
                                    label=f"PANAS Item {idx + 1}",
                                    interactive=True
                                )
                            )
                    with gr.Column():
                        for idx in range(10, 20):
                            panas_item_components.append(
                                gr.Radio(
                                    choices=[1, 2, 3, 4, 5],
                                    value=3,
                                    label=f"PANAS Item {idx + 1}",
                                    interactive=True
                                )
                            )
                panas_submit_btn = gr.Button("Submit PANAS", variant="secondary")
                panas_status = gr.Markdown("", visible=False)

            with gr.Column(visible=False) as sus_panel:
                sus_title = gr.Markdown("### System Usability Scale")
                sus_item_components = []
                for idx in range(10):
                    sus_item_components.append(
                        gr.Radio(
                            choices=[1, 2, 3, 4, 5],
                            value=3,
                            label=f"SUS Item {idx + 1}",
                            interactive=True
                        )
                    )
                sus_submit_btn = gr.Button("Submit SUS", variant="secondary")
                sus_status = gr.Markdown("", visible=False)

            with gr.Column(visible=False) as therapy_panel:
                therapy_title = gr.Markdown("### Therapy Experience")
                therapy_item_components = []
                for _, en_label, _ in THERAPY_EXPERIENCE_ITEMS:
                    therapy_item_components.append(
                        gr.Radio(
                            choices=[1, 2, 3, 4, 5, 6, 7],
                            value=4,
                            label=en_label,
                            interactive=True
                        )
                    )
                therapy_submit_btn = gr.Button("Submit Experience", variant="secondary")
                therapy_status = gr.Markdown("", visible=False)
             
            # 缁熶竴鐨勬秷鎭緭鍏ユ
            msg_input = gr.Textbox(
                label="Share your thoughts...",
                placeholder="Type your message here...",
                container=True,
                elem_id="gim-chat-input"
            )
            
            # 鎸夐挳琛?            with gr.Row():
                toggle_memory_btn = gr.Button("馃懁 Memory", scale=0)
                submit_btn = gr.Button("Send", variant="primary", elem_id="gim-submit-btn")
                finish_session_btn = gr.Button("Finish Session", variant="secondary", interactive=False)
                toggle_program_btn = gr.Button("馃幍 Program", scale=0)
            
            with gr.Row():
                clear_btn = gr.Button("Clear Conversation")
                save_btn = gr.Button("Save Session")
            
            # 淇濆瓨鐘舵€佹枃鏈?            save_info = gr.Textbox(label="Save Status", interactive=False)
        
        # 鍙充晶鎶藉眽 - Music Program淇℃伅
        with gr.Column(scale=1, visible=False, elem_classes="right-drawer") as right_drawer:
            with gr.Column(elem_classes="drawer-content"):
                program_title_en = gr.Markdown("### 馃幍 Music Program", visible=True)
                program_title_zh = gr.Markdown("### 馃幍 闊充箰绋嬪簭", visible=False)
                
                # 杩涘害鏄剧ず鍖哄煙
                progress_display = gr.Markdown("Waiting to generate music...\n绛夊緟鐢熸垚闊充箰...", elem_classes="progress-indicator")
                
                # Program淇℃伅鏄剧ず鍖哄煙
                program_info_display = gr.Markdown("Program details will be displayed here.\n绋嬪簭璇︽儏灏嗗湪姝ゆ樉绀恒€?)
    
    def toggle_drawer(is_visible):
        """鍒囨崲鎶藉眽鍙鎬х殑鍑芥暟"""
        return gr.update(visible=not is_visible)

    def build_washout_display(user_id: str, washout: dict = None, session: GIMTherapySession = None):
        washout = washout or {}
        end_epoch = washout.get("washout_end_epoch")
        end_text = format_participant_timestamp(washout.get("washout_end") or "")
        if not end_epoch:
            return ""

        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        remaining_seconds = max(0, int(float(end_epoch) - time.time()))
        remaining_text = format_remaining_time(remaining_seconds)
        return f"""
<div style="padding:12px;border:1px solid #ddd;border-radius:6px;background:#fafafa;">
  <p><strong>{texts["washout_title"]}</strong></p>
  <p>{texts["washout_id_intro"]}</p>
  <p style="font-size:1.4rem;font-weight:700;letter-spacing:0.04em;">{user_id}</p>
  <p>{texts["washout_instruction"]}</p>
  <p>{texts["washout_available_after"].format(end_time=end_text)}</p>
  <p style="font-size:1.6rem;font-weight:700;">{texts["washout_remaining"]} {remaining_text}</p>
  <p>{texts["washout_done"] if remaining_seconds <= 0 else texts["washout_continue"].format(minutes=remaining_seconds // 60, seconds=remaining_seconds % 60, participant_id=user_id)}</p>
</div>
"""

    def format_participant_timestamp(value):
        if not value:
            return ""
        return str(value).replace("T", " ")

    def format_remaining_time(seconds):
        seconds = max(0, int(seconds or 0))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def build_participant_save_message(user_id: str, session: GIMTherapySession = None):
        is_chinese = is_session_chinese(session)
        return get_ui_texts(is_chinese)["save_participant_id"].format(participant_id=user_id)

    def build_final_completion_display(session: GIMTherapySession = None):
        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        return f"""
<div class="final-completion-card">
  <h2>{texts["final_complete_title"]}</h2>
  <p>{texts["final_complete_body"]}</p>
</div>
"""

    def is_final_completion(session: GIMTherapySession):
        if not session:
            return False
        total_sessions = len(session.condition_order or [session.condition])
        usability = session.session_data.get("usability", {})
        return (
            session.session_number >= total_sessions
            and usability.get("therapy_experience") is not None
        )

    def get_experiment_screen_updates(session: GIMTherapySession):
        if is_final_completion(session):
            return (
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(value=build_final_completion_display(session), visible=True),
            )

        if getattr(session, "washout_pending", False):
            return (
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(value="", visible=False),
            )

        if not getattr(session, "experiment_started", False):
            return (
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(value="", visible=False),
            )

        return (
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(value="", visible=False),
        )

    def get_washout_timer_update(active: bool):
        return (gr.update(active=bool(active)),) if washout_timer else ()

    def refresh_washout_screen(session: GIMTherapySession):
        if not session or not getattr(session, "washout_pending", False):
            return (
                gr.update(value="", visible=False),
                gr.update(),
                gr.update(),
                *get_washout_timer_update(False),
            )

        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        end_epoch = session.session_data.get("washout_end_epoch")
        try:
            still_active = bool(end_epoch and float(end_epoch) > time.time())
        except (TypeError, ValueError):
            still_active = False
        if still_active:
            return (
                gr.update(value=build_washout_display(session.user_id, session.session_data, session=session), visible=True),
                gr.update(value=texts["start_session_2"]),
                gr.update(),
                *get_washout_timer_update(True),
            )

        return (
            gr.update(value="", visible=False),
            gr.update(value=texts["start_session_2"]),
            gr.update(visible=True),
            *get_washout_timer_update(False),
        )

    def build_participant_status_message(session: GIMTherapySession):
        if not session:
            return ""

        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)

        if is_final_completion(session):
            return ""

        if getattr(session, "washout_pending", False):
            return ""

        if getattr(session, "session_completed", False):
            total_sessions = len(session.condition_order or [session.condition])
            if session.session_number >= total_sessions and session.panas_state.get("post_session_done"):
                return texts["all_sessions_complete"]
            return ""

        return texts["participant_id_generated"].format(session_number=session.session_number)

    def get_audio_player_update(session: GIMTherapySession):
        if not session or not session.selected_music_tracks:
            return gr.update(value=None, visible=False)

        audio_path = session.selected_music_tracks[0].get("full_path")
        if audio_path:
            audio_path = os.path.abspath(audio_path)
        if audio_path and os.path.exists(audio_path) and os.path.isfile(audio_path):
            print(f"Audio player path: {audio_path}")
            return gr.update(value=audio_path, visible=True)

        print(f"WARNING: Audio path not found for player: {audio_path}")
        return gr.update(value=None, visible=False)

    def get_chat_input_updates(session: GIMTherapySession):
        """Hide chat input controls until both pre-session assessments are complete."""
        if not session:
            session = GIMTherapySession()

        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        ready = session.sam_state.get("pre_session_done") and session.panas_state.get("pre_session_done")
        if getattr(session, "session_completed", False):
            ready = False
        return (
            gr.update(
                visible=bool(ready),
                interactive=bool(ready),
                label=texts["input_label"],
                placeholder=texts["input_placeholder"]
            ),
            gr.update(visible=bool(ready), value=texts["submit"])
        )

    def get_finish_session_button_update(session: GIMTherapySession):
        return gr.update(interactive=bool(session and getattr(session, "session_completed", False)))

    def finish_session(session: GIMTherapySession):
        if not session:
            session = GIMTherapySession()

        if not getattr(session, "session_completed", False):
            return (
                session.chat_history,
                session,
                *get_sam_ui_updates(session),
                *get_panas_ui_updates(session),
                *get_chat_input_updates(session),
                *get_sus_ui_updates(session, reset_inputs=True),
                *get_therapy_ui_updates(session, reset_inputs=True)
            )

        session.mark_ready_for_post_session_assessment()

        return (
            session.chat_history,
            session,
            *get_sam_ui_updates(session),
            *get_panas_ui_updates(session),
            *get_chat_input_updates(session),
            *get_sus_ui_updates(session, reset_inputs=True),
            *get_therapy_ui_updates(session, reset_inputs=True)
        )

    def get_sam_ui_updates(session: GIMTherapySession, status_message: str = "", reset_sliders: bool = False):
        """Build visibility and content updates for the minimal SAM panel."""
        if not session:
            session = GIMTherapySession()

        session.ensure_sam_pending_phase()
        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        pending_phase = session.sam_state.get("pending_phase")

        if pending_phase == "pre_session" and not session.sam_state.get("pre_session_done"):
            title = texts["sam_title_pre"]
            visible = True
        elif pending_phase == "post_session" and not session.sam_state.get("post_session_done"):
            title = texts["sam_title_post"]
            visible = True
        else:
            title = texts["sam_title_default"]
            visible = False

        valence_update = (
            gr.update(label=texts["sam_valence_label"], info=texts["sam_valence_info"], value=5)
            if reset_sliders else
            gr.update(label=texts["sam_valence_label"], info=texts["sam_valence_info"])
        )
        arousal_update = (
            gr.update(label=texts["sam_arousal_label"], info=texts["sam_arousal_info"], value=5)
            if reset_sliders else
            gr.update(label=texts["sam_arousal_label"], info=texts["sam_arousal_info"])
        )
        status_update = gr.update(value=status_message, visible=bool(status_message))

        return (
            gr.update(visible=visible),
            gr.update(value=title),
            gr.update(value=texts["sam_instruction"], visible=visible),
            valence_update,
            arousal_update,
            status_update
        )

    def submit_sam_rating(session: GIMTherapySession, valence: int, arousal: int):
        """Store a SAM rating without affecting the existing chat/music flow."""
        if not session:
            session = GIMTherapySession()

        session.ensure_sam_pending_phase()
        pending_phase = session.sam_state.get("pending_phase")
        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)

        if pending_phase not in ("pre_session", "post_session"):
            status_message = texts["sam_not_needed"]
            return (
                session,
                *get_sam_ui_updates(session, status_message=status_message),
                *get_panas_ui_updates(session)
            )

        rating_record = {
            "phase": pending_phase,
            "valence": int(valence),
            "arousal": int(arousal),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        session.sam_state["ratings"].append(rating_record)
        sam_payload = {
            "valence": rating_record["valence"],
            "arousal": rating_record["arousal"],
            "timestamp": rating_record["timestamp"]
        }
        if pending_phase == "pre_session":
            session.session_data["pre_session"]["sam"] = sam_payload
        elif pending_phase == "post_session":
            session.session_data["post_session"]["sam"] = sam_payload
        else:
            track_index = len(session.session_data.get("music_sequence", []))
            if track_index > 0:
                update_music_track_feedback(
                    session.session_data,
                    track_index,
                    rating_record["valence"],
                    rating_record["arousal"],
                    None,
                    None
                )

        if pending_phase == "pre_session":
            session.sam_state["pre_session_done"] = True
        elif pending_phase == "post_session":
            session.sam_state["post_session_done"] = True

        session.sam_state["pending_phase"] = None
        session.ensure_sam_pending_phase()
        session.ensure_panas_pending_phase()

        status_message = texts["sam_saved"]
        return (
            session,
            *get_sam_ui_updates(session, status_message=status_message, reset_sliders=True),
            *get_panas_ui_updates(session, reset_inputs=True)
        )

    def get_panas_ui_updates(session: GIMTherapySession, status_message: str = "", reset_inputs: bool = False):
        """Build visibility and content updates for the minimal PANAS panel."""
        if not session:
            session = GIMTherapySession()

        session.ensure_panas_pending_phase()
        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        pending_phase = session.panas_state.get("pending_phase")

        if pending_phase == "pre_session" and not session.panas_state.get("pre_session_done"):
            title = texts["panas_title_pre"]
            visible = True
        elif pending_phase == "post_session" and not session.panas_state.get("post_session_done"):
            title = texts["panas_title_post"]
            visible = True
        else:
            title = texts["panas_title_default"]
            visible = False

        item_updates = []
        current_order = session.panas_state.get("current_order", [])
        for idx in range(20):
            item_key = current_order[idx] if idx < len(current_order) else None
            label = session.get_panas_item_label(item_key, is_chinese) if item_key else (f"鏉＄洰 {idx + 1}" if is_chinese else f"PANAS Item {idx + 1}")
            update_kwargs = {"label": label}
            if reset_inputs:
                update_kwargs["value"] = 3
            item_updates.append(gr.update(**update_kwargs))

        status_update = gr.update(value=status_message, visible=bool(status_message))

        return (
            gr.update(visible=visible),
            gr.update(value=title),
            *item_updates,
            status_update
        )

    def get_sus_ui_updates(session: GIMTherapySession, status_message: str = "", reset_inputs: bool = False):
        """Build visibility and content updates for the SUS panel."""
        if not session:
            session = GIMTherapySession()

        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        usability = session.session_data.get("usability", {})
        sus_done = usability.get("sus_responses") is not None
        visible = bool(session.panas_state.get("post_session_done")) and not sus_done and not getattr(session, "washout_pending", False)

        item_updates = []
        for idx, labels in enumerate(SUS_ITEMS):
            label = labels[1] if is_chinese else labels[0]
            update_kwargs = {"label": f"S{idx + 1}. {label}"}
            if reset_inputs:
                update_kwargs["value"] = 3
            item_updates.append(gr.update(**update_kwargs))

        return (
            gr.update(visible=visible),
            gr.update(value=texts["sus_title"] if visible else texts["sus_title_default"]),
            *item_updates,
            gr.update(value=status_message, visible=bool(status_message))
        )

    def calculate_sus_score(responses):
        score = 0
        for idx, response in enumerate(responses):
            if idx % 2 == 0:
                score += response - 1
            else:
                score += 5 - response
        return score * 2.5

    def submit_sus_rating(session: GIMTherapySession, *responses):
        """Store SUS responses and reveal the therapy experience form."""
        if not session:
            session = GIMTherapySession()

        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        usability = session.session_data.setdefault("usability", {})
        if not session.panas_state.get("post_session_done"):
            return (
                session,
                *get_sus_ui_updates(session, status_message=texts["sus_not_needed"]),
                *get_therapy_ui_updates(session)
            )

        sus_responses = [int(response) if response is not None else 3 for response in responses[:10]]
        usability["sus_responses"] = sus_responses
        usability["sus_score"] = calculate_sus_score(sus_responses)

        return (
            session,
            *get_sus_ui_updates(session, status_message=texts["sus_saved"], reset_inputs=True),
            *get_therapy_ui_updates(session, reset_inputs=True)
        )

    def get_therapy_ui_updates(session: GIMTherapySession, status_message: str = "", reset_inputs: bool = False):
        """Build visibility and content updates for the therapy experience panel."""
        if not session:
            session = GIMTherapySession()

        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        usability = session.session_data.get("usability", {})
        sus_done = usability.get("sus_responses") is not None
        therapy_done = usability.get("therapy_experience") is not None
        visible = bool(sus_done) and not therapy_done and not getattr(session, "washout_pending", False)

        item_updates = []
        for _, en_label, zh_label in THERAPY_EXPERIENCE_ITEMS:
            update_kwargs = {"label": zh_label if is_chinese else en_label}
            if reset_inputs:
                update_kwargs["value"] = 4
            item_updates.append(gr.update(**update_kwargs))

        return (
            gr.update(visible=visible),
            gr.update(value=texts["therapy_title"] if visible else texts["therapy_title_default"]),
            *item_updates,
            gr.update(value=status_message, visible=bool(status_message))
        )

    def submit_therapy_experience_rating(session: GIMTherapySession, *ratings):
        """Store therapy experience responses and persist the completed session export."""
        if not session:
            session = GIMTherapySession()

        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)
        usability = session.session_data.setdefault("usability", {})
        if usability.get("sus_responses") is None:
            return (
                session,
                *get_therapy_ui_updates(session, status_message=texts["therapy_not_needed"]),
                *get_experiment_screen_updates(session),
                gr.update(value=build_participant_status_message(session)),
                gr.update(value="", visible=False),
                *get_washout_timer_update(False)
            )

        usability["therapy_experience"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "items": {
                item_key: int(ratings[idx]) if idx < len(ratings) and ratings[idx] is not None else 4
                for idx, (item_key, _, _) in enumerate(THERAPY_EXPERIENCE_ITEMS)
            }
        }
        export_session_results(session)

        return (
            session,
            *get_therapy_ui_updates(session, status_message=texts["therapy_saved"], reset_inputs=True),
            *get_experiment_screen_updates(session),
            gr.update(value=build_participant_status_message(session)),
            gr.update(value="", visible=False),
            *get_washout_timer_update(False)
        )

    def submit_panas_rating(session: GIMTherapySession, *ratings):
        """Store a PANAS rating without affecting existing chat/music/SAM flow."""
        if not session:
            session = GIMTherapySession()

        session.ensure_panas_pending_phase()
        pending_phase = session.panas_state.get("pending_phase")
        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)

        if pending_phase not in ("pre_session", "post_session"):
            status_message = texts["panas_not_needed"]
            return (
                session,
                *get_panas_ui_updates(session, status_message=status_message),
                *get_chat_input_updates(session),
                *get_sus_ui_updates(session),
                *get_therapy_ui_updates(session),
                gr.update(value=""),
                gr.update(value="", visible=False),
                gr.update(value=session.user_id),
                *get_experiment_screen_updates(session),
                *get_washout_timer_update(False)
            )

        current_order = session.panas_state.get("current_order", [])
        items = {}
        for idx, item_key in enumerate(current_order):
            rating_value = ratings[idx] if idx < len(ratings) and ratings[idx] is not None else 3
            items[item_key] = int(rating_value)

        pa_score = sum(items.get(item_key, 0) for item_key in PANAS_POSITIVE_ITEMS)
        na_score = sum(items.get(item_key, 0) for item_key in PANAS_NEGATIVE_ITEMS)

        panas_record = {
            "phase": pending_phase,
            "items": items,
            "pa_score": pa_score,
            "na_score": na_score,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        session.panas_state["panas"].append(panas_record)
        if pending_phase == "pre_session":
            session.session_data["pre_session"]["panas"] = copy.deepcopy(panas_record)
        elif pending_phase == "post_session":
            session.session_data["post_session"]["panas"] = copy.deepcopy(panas_record)
        print("PANAS RECORD:", pending_phase, pa_score, na_score)

        if pending_phase == "pre_session":
            session.panas_state["pre_session_done"] = True
        elif pending_phase == "post_session":
            session.panas_state["post_session_done"] = True

        participant_message = ""
        countdown_html = ""
        if pending_phase == "post_session" and session.session_number == 1:
            washout = start_washout(session.user_id)
            session.washout_pending = True
            session.session_data.update({
                "washout_start": washout.get("washout_start"),
                "washout_end": washout.get("washout_end"),
                "washout_start_epoch": washout.get("washout_start_epoch"),
                "washout_end_epoch": washout.get("washout_end_epoch"),
            })
            participant_message = ""
            countdown_html = build_washout_display(session.user_id, washout, session=session)

        if pending_phase == "post_session":
            export_session_results(session)
            session.append_ablation_log_once()

        session.panas_state["pending_phase"] = None
        session.panas_state["current_order"] = []
        session.panas_state["current_order_phase"] = None
        session.ensure_panas_pending_phase()

        status_message = texts["panas_saved"]
        return (
            session,
            *get_panas_ui_updates(session, status_message=status_message, reset_inputs=True),
            *get_chat_input_updates(session),
            *get_sus_ui_updates(session, reset_inputs=True),
            *get_therapy_ui_updates(session, reset_inputs=True),
            gr.update(value=participant_message),
            gr.update(value=countdown_html, visible=bool(countdown_html)),
            gr.update(value=session.user_id),
            *get_experiment_screen_updates(session),
            *get_washout_timer_update(bool(countdown_html))
        )

    def format_memory_for_display(session: GIMTherapySession):
        """鏍煎紡鍖栧苟杩斿洖鐢ㄦ埛璁板繂鐨凪arkdown鏂囨湰"""
        if not session or not session.memory:
            return "No memory data yet.\n鏆傛棤璁板繂鏁版嵁銆?
        
        # 鑾峰彇褰撳墠璇█
        is_chinese = is_session_chinese(session)
        
        if is_chinese:
            memory_text = "#### 鏈€杩戞儏缁姸鎬侊細\n"
            emotions = [m.get('emotion', '鏈煡') for m in session.memory.memories.get('emotional_states', [])[-3:]]
            if emotions:
                memory_text += "- " + "\n- ".join(emotions)
            else:
                memory_text += "鏆傛棤璁板綍"
            
            memory_text += "\n\n#### 鐒︾偣鎰忓浘锛歕n"
            focuses = [f.get('focus', '鏈煡') for f in session.memory.memories.get('focus_intentions', [])[-2:]]
            if focuses:
                memory_text += "- " + "\n- ".join(focuses)
            else:
                memory_text += "鏆傛棤璁板綍"
                
            memory_text += "\n\n#### 闊充箰鍋忓ソ锛歕n"
            prefs = session.memory.memories.get('preferences', {}).get('music', [])
            if prefs:
                for pref in prefs[-3:]:
                    sentiment = "鍠滄" if pref.get('sentiment') == 'like' else "涓嶅枩娆?
                    memory_text += f"- {sentiment} {pref.get('genre', '鏈煡椋庢牸')}\n"
            else:
                memory_text += "鏆傛棤璁板綍"
        else:
            memory_text = "#### Recent Emotions:\n"
            emotions = [m.get('emotion', 'unknown') for m in session.memory.memories.get('emotional_states', [])[-3:]]
            if emotions:
                memory_text += "- " + "\n- ".join(emotions)
            else:
                memory_text += "No records yet"
            
            memory_text += "\n\n#### Focus Intentions:\n"
            focuses = [f.get('focus', 'unknown') for f in session.memory.memories.get('focus_intentions', [])[-2:]]
            if focuses:
                memory_text += "- " + "\n- ".join(focuses)
            else:
                memory_text += "No records yet"
                
            memory_text += "\n\n#### Music Preferences:\n"
            prefs = session.memory.memories.get('preferences', {}).get('music', [])
            if prefs:
                for pref in prefs[-3:]:
                    sentiment = "Likes" if pref.get('sentiment') == 'like' else "Dislikes"
                    memory_text += f"- {sentiment} {pref.get('genre', 'unknown genre')}\n"
            else:
                memory_text += "No records yet"
        
        return memory_text

    def format_program_for_display(session: GIMTherapySession):
        """鏍煎紡鍖栧苟杩斿洖Music Program淇℃伅鐨凪arkdown鏂囨湰"""
        if not session or not session.gim_program_result:
            is_chinese = is_session_chinese(session)
            return "No program generated yet.\n鏆傛湭鐢熸垚绋嬪簭銆? if is_chinese else "No program generated yet."
        
        result = session.gim_program_result
        is_chinese = is_session_chinese(session)
        
        if is_chinese:
            program_text = f"#### 娌荤枟鐩爣: {result['analysis'].get('therapeutic_goal', '鎯呮劅鎺㈢储')}\n\n"
            program_text += f"#### 褰撳墠鎯呯华: {result['analysis'].get('current_emotion', '寰呮帰绱?)}\n\n"
            program_text += "#### Program闃舵:\n"
            for i, phase in enumerate(result.get('program', []), 1):
                program_text += f"{i}. **{phase.get('phase', f'闃舵{i}')}** ({phase.get('duration_seconds', 0)}绉?\n"
                program_text += f"   - 鐩殑: {phase.get('purpose', '鏈鏄?)}\n\n"
            
            if 'output' in result:
                program_text += f"#### 鍚堟垚淇℃伅:\n"
                program_text += f"- 鎬绘椂闀? {result['output'].get('total_seconds', 0)}绉抃n"
                program_text += f"- 鏂囦欢: {os.path.basename(result['output'].get('file', ''))}\n"
        else:
            program_text = f"#### Therapeutic Goal: {result['analysis'].get('therapeutic_goal', 'Emotional exploration')}\n\n"
            program_text += f"#### Current Emotion: {result['analysis'].get('current_emotion', 'To be explored')}\n\n"
            program_text += "#### Program Phases:\n"
            for i, phase in enumerate(result.get('program', []), 1):
                program_text += f"{i}. **{phase.get('phase', f'Phase {i}')}** ({phase.get('duration_seconds', 0)}s)\n"
                program_text += f"   - Purpose: {phase.get('purpose', 'Not specified')}\n\n"
            
            if 'output' in result:
                program_text += f"#### Synthesis Info:\n"
                program_text += f"- Total duration: {result['output'].get('total_seconds', 0)}s\n"
                program_text += f"- File: {os.path.basename(result['output'].get('file', ''))}\n"
        
        return program_text
    

    def process_dialogue_stream(message: str, session: GIMTherapySession):
        """鍙鐞嗗璇濇祦"""
        if not session:
            session = GIMTherapySession()
            
        session.append_chat_message("user", message)
        streamed_response = ""
        for chunk in session.get_next_response_stream(message):
            chunk_text = session._normalize_message_content(chunk)
            streamed_response += chunk_text
            yield (
                session.chat_history + [{"role": "assistant", "content": streamed_response}],
                session,
                get_finish_session_button_update(session)
            )
        yield session.chat_history, session, get_finish_session_button_update(session)

    def generate_music_stream(session: GIMTherapySession):
        """鍦ㄩ渶瑕佹椂锛屾祦寮忕敓鎴愰煶涔愬苟鏇存柊UI"""
        print("DEBUG current_state =", session.current_state)
        print("DEBUG music_selected =", session.music_selected)
        session.ensure_sam_pending_phase()
        session.ensure_panas_pending_phase()
        is_chinese = is_session_chinese(session)
        texts = get_ui_texts(is_chinese)

        # 鍙湁鍦?music_imaging 闃舵涓旈煶涔愭湭閫夋嫨鏃舵墠鎵ц
        if session.current_state != GIMState.MUSIC_IMAGING or session.music_selected:
            yield (
                session.chat_history,
                session,
                format_memory_for_display(session),
                format_program_for_display(session),
                "Music generation not required.\n鏃犻渶鐢熸垚闊充箰銆?,
                get_audio_player_update(session),
                *get_sam_ui_updates(session),
                *get_panas_ui_updates(session),
                *get_chat_input_updates(session),
                get_finish_session_button_update(session)
            )
            return

        # 瀹氫箟鍥炶皟鍑芥暟锛岀敤浜庡湪闊充箰鐢熸垚鏃舵洿鏂皊ession鐘舵€?        def progress_callback(stage, status, progress, data=None):
            is_chinese = is_session_chinese(session)
            callback_texts = get_ui_texts(is_chinese)
            
            stage_names = {
                "analysis": "鍒嗘瀽" if is_chinese else "Analysis",
                "design": "璁捐" if is_chinese else "Design", 
                "music_search": "闊充箰妫€绱? if is_chinese else "Music Search",
                "processing": "闊充箰澶勭悊" if is_chinese else "Music Processing",
                "synthesis": "闊抽鍚堟垚" if is_chinese else "Audio Synthesis"
            }
            
            session.progress_status = f"**{stage_names.get(stage, stage)}**: {status} ({progress:.0f}%)"

            if stage == "processing":
                session.append_guidance_message_once("music_processing_started", callback_texts["music_processing"])
            
            if data and stage == "synthesis" and progress == 100:
                session.gim_program_result = data
        
        # 浣跨敤绾跨▼杩愯鑰楁椂浠诲姟
        import threading
        session.append_guidance_message_once("music_generation_started", texts["music_start"])
        music_generation_thread = threading.Thread(
            target=session.select_music_for_imaging,
            kwargs={'progress_callback': progress_callback}
        )
        music_generation_thread.start()

        # 涓荤嚎绋嬪惊鐜鏌ヨ繘搴﹀苟yield鏇存柊
        while music_generation_thread.is_alive():
            time.sleep(0.5)  # 姣?.5绉掓洿鏂颁竴娆I
            yield (
                session.chat_history, 
                session, 
                format_memory_for_display(session), 
                format_program_for_display(session),
                session.progress_status,
                get_audio_player_update(session),
                *get_sam_ui_updates(session),
                *get_panas_ui_updates(session),
                *get_chat_input_updates(session),
                get_finish_session_button_update(session)
            )
        
        # 浠诲姟瀹屾垚
        if session.selected_music_tracks or session.gim_program_result:
            session.music_selected = True
        else:
            session.music_selected = False
        
        # 灏嗛煶涔愭挱鏀惧櫒娣诲姞鍒板璇濆巻鍙?        if session.selected_music_tracks:
            audio_path = session.selected_music_tracks[0].get('full_path')
            if audio_path:
                audio_path = os.path.abspath(audio_path)
            if audio_path and os.path.exists(audio_path):
                session.append_guidance_message_once("music_ready", texts["music_ready"])
                session.append_guidance_message_once("music_experience", texts["music_experience"])
                # 娣诲姞闊抽鏂囦欢浣滀负鍗曠嫭鐨勬秷鎭?        #        session.chat_history.append({"role": "assistant", "content": (audio_path,)})
                session.append_chat_message(
                    "assistant",
                    texts["play_music"],
                    phase=GIMState.MUSIC_IMAGING
                )

        # 鏈€缁堢殑UI鏇存柊
        is_chinese = is_session_chinese(session)
        completed_message = "**瀹屾垚!** 鎮ㄧ殑闊充箰宸插噯澶囧氨缁€? if is_chinese else "**Completed!** Your music is ready."
        yield (
            session.chat_history, 
            session, 
            format_memory_for_display(session),
            format_program_for_display(session),
            completed_message,
            get_audio_player_update(session),
            *get_sam_ui_updates(session),
            *get_panas_ui_updates(session),
            *get_chat_input_updates(session),
            get_finish_session_button_update(session)
        )

    # 鍒濆鍖栧嚱鏁?    def initialize_session():
        init_started_at = time.time()
        print("[startup] initialize_session start")
        participant_id = generate_participant_id()
        info = get_next_session_info(participant_id)
        session_id = init_ablation_session(info["user_id"], info["condition"])
        session = GIMTherapySession(
            startup_light=True,
            user_id=info["user_id"],
            condition=info["condition"],
            condition_order=info["condition_order"],
            session_number=info["session_number"],
            session_id=session_id,
        )
        print(f"[startup] initialize_session completed in {time.time() - init_started_at:.3f}s")
        return (
            session,
            gr.update(value=session.user_id),
            gr.update(value=build_participant_status_message(session)),
            gr.update(value="", visible=False),
            *get_experiment_screen_updates(session),
            *get_washout_timer_update(False),
            session.chat_history,
            format_memory_for_display(session),
            format_program_for_display(session),
            gr.update(value=None, visible=False),
            *get_sam_ui_updates(session),
            *get_panas_ui_updates(session),
            *get_chat_input_updates(session),
            get_finish_session_button_update(session),
            *get_sus_ui_updates(session, reset_inputs=True),
            *get_therapy_ui_updates(session, reset_inputs=True)
        )

    def start_participant_session(user_id: str, current_session: GIMTherapySession = None):
        info = get_next_session_info(user_id)
        washout = info.get("washout") or {}
        language = getattr(current_session, "language", "en")
        is_chinese = language == "zh"
        texts = get_ui_texts(is_chinese)
        if info.get("washout_required"):
            session = GIMTherapySession(
                startup_light=True,
                user_id=info["user_id"],
                condition=info["condition"],
                condition_order=info["condition_order"],
                session_number=info["session_number"],
                language=language,
            )
            session.experiment_started = True
            session.session_completed = True
            session.washout_pending = True
            session.sam_state["pending_phase"] = None
            session.sam_state["pre_session_done"] = True
            session.sam_state["post_session_done"] = True
            session.panas_state["pre_session_done"] = True
            session.panas_state["post_session_done"] = True
            remaining = washout.get("remaining_seconds", 0)
            status = ""
            return (
                session,
                gr.update(value=info["user_id"]),
                session.chat_history,
                format_memory_for_display(session),
                format_program_for_display(session),
                gr.update(value=status),
                gr.update(value=build_washout_display(info["user_id"], washout, session=session), visible=True),
                gr.update(value=None, visible=False),
                *get_experiment_screen_updates(session),
                *get_washout_timer_update(True),
                *get_sam_ui_updates(session, reset_sliders=True),
                *get_panas_ui_updates(session, reset_inputs=True),
                *get_chat_input_updates(session),
                get_finish_session_button_update(session),
                *get_sus_ui_updates(session, reset_inputs=True),
                *get_therapy_ui_updates(session, reset_inputs=True)
            )

        session_id = init_ablation_session(info["user_id"], info["condition"])
        session = GIMTherapySession(
            startup_light=True,
            user_id=info["user_id"],
            condition=info["condition"],
            condition_order=info["condition_order"],
            session_number=info["session_number"],
            session_id=session_id,
            language=language,
        )
        session.experiment_started = True
        if info["all_sessions_complete"]:
            session.session_completed = True
            session.sam_state["pending_phase"] = None
            session.sam_state["pre_session_done"] = True
            session.sam_state["post_session_done"] = True
            session.panas_state["pre_session_done"] = True
            session.panas_state["post_session_done"] = True
            status = texts["all_sessions_complete"]
        else:
            status = texts["session_ready"].format(session_number=info["session_number"])

        return (
            session,
            gr.update(value=info["user_id"]),
            session.chat_history,
            format_memory_for_display(session),
            format_program_for_display(session),
            gr.update(value=status),
            gr.update(value="", visible=False),
            gr.update(value=None, visible=False),
            *get_experiment_screen_updates(session),
            *get_washout_timer_update(False),
            *get_sam_ui_updates(session, reset_sliders=True),
            *get_panas_ui_updates(session, reset_inputs=True),
            *get_chat_input_updates(session),
            get_finish_session_button_update(session),
            *get_sus_ui_updates(session, reset_inputs=True),
            *get_therapy_ui_updates(session, reset_inputs=True)
        )
    
    # 娓呴櫎瀵硅瘽鍑芥暟
    def clear_conversation(session: GIMTherapySession):
        """娓呴櫎瀵硅瘽浣嗕繚鐣欒蹇?""
        if not session:
            session = GIMTherapySession()
            
        # 閲嶇疆褰撳墠鐘舵€佷负PRELUDE
        session.current_state = GIMState.PRELUDE
        session.timestamp_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        session.music_selected = False
        session.selected_music_tracks = []
        session.gim_program_result = None
        session.kimusic_render_result = None
        session.baseline_used_track_ids = set()
        session.ablation_logged = False
        session.washout_pending = False
        
        # 娓呴櫎杩涘害鏄剧ず鐩稿叧灞炴€?        session.progress_status = ""
        session.phase_info = ""
        session.music_info = ""
        
        # 閲嶆柊鍒濆鍖栨杩庢秷鎭?        session.chat_history = []
        session.session_data = create_empty_session(user_id=session.user_id, condition=session.condition)
        session.session_data["condition_order"] = list(session.condition_order or [])
        session.session_data["session_number"] = session.session_number
        session.session_data["timestamp_start"] = session.timestamp_start
        session.initialize_welcome_message()
        session.sam_state = {
            "ratings": [],
            "pending_phase": "pre_session",
            "pre_session_done": False,
            "post_session_done": False
        }
        session.session_completed = False
        session.panas_state = {
            "panas": [],
            "pending_phase": None,
            "pre_session_done": False,
            "post_session_done": False,
            "current_order": [],
            "current_order_phase": None
        }
        session.ui_message_flags = {
            "music_generation_started": False,
            "music_processing_started": False,
            "music_ready": False,
            "music_experience": False,
            "music_ended": False,
            "post_music_reflection": False,
            "closure": False,
            "final_assessment_intro": False
        }
        session.postlude_reflection_prompt_index = None
        
        return (
            session.chat_history,
            session,
            format_memory_for_display(session),
            format_program_for_display(session),
            gr.update(value=None, visible=False),
            *get_experiment_screen_updates(session),
            *get_washout_timer_update(False),
            *get_sam_ui_updates(session, reset_sliders=True),
            *get_panas_ui_updates(session, reset_inputs=True),
            *get_chat_input_updates(session),
            get_finish_session_button_update(session),
            *get_sus_ui_updates(session, reset_inputs=True),
            *get_therapy_ui_updates(session, reset_inputs=True)
        )
    
    # 淇濆瓨浼氳瘽鍑芥暟
    def save_session(session: GIMTherapySession):
        if not session:
            is_chinese = is_session_chinese(session)
            return "閿欒锛氫細璇濇湭鍒濆鍖? if is_chinese else "Error: Session not initialized"
        if session.memory:
            memories_dir = os.path.join("data", "memories")
            os.makedirs(memories_dir, exist_ok=True)
            session_id = session.session_data.get("session_id", "session")
            memory_filename = f"{session.user_id}_{session_id}.json"
            session.memory.save_memories_to_file(os.path.join(memories_dir, memory_filename))
        is_chinese = is_session_chinese(session)
        return "浼氳瘽宸叉垚鍔熶繚瀛橈紒" if is_chinese else "Session saved successfully!"
    
    # 璇█鍒囨崲澶勭悊鍑芥暟
    def change_language(lang_choice: str, session: GIMTherapySession):
        """鍒囨崲鐣岄潰璇█骞舵洿鏂版彁绀烘ā鏉?""
        if not session:
            session = GIMTherapySession()

        # 淇濆瓨鐪熷疄瀵硅瘽鍘嗗彶锛堥櫎浜嗘杩庢秷鎭級
        true_chat_history = session.chat_history[1:] if len(session.chat_history) > 1 else []

        # 璁剧疆璇█
        is_chinese = lang_choice != "English"
        session.language = "zh" if is_chinese else "en"
        
        # 閲嶆柊鍒濆鍖栨杩庢秷鎭?        session.chat_history = []
        session.initialize_welcome_message()
        
        # 鎭㈠鐪熷疄瀵硅瘽鍘嗗彶
        session.chat_history.extend(true_chat_history)
        session.ensure_sam_pending_phase()
        session.ensure_panas_pending_phase()
        
        # 鍑嗗UI鏂囨湰
        ui_text = {
            "clear": "娓呴櫎瀵硅瘽" if is_chinese else "Clear Conversation",
            "save": "淇濆瓨浼氳瘽" if is_chinese else "Save Session",
            "memory": "馃懁 璁板繂" if is_chinese else "馃懁 Memory",
            "program": "馃幍 绋嬪簭" if is_chinese else "馃幍 Program",
            "save_status": "淇濆瓨鐘舵€? if is_chinese else "Save Status"
        }
        new_texts = get_ui_texts(is_chinese)
        chat_input_update, submit_update = get_chat_input_updates(session)
        washout_display_value = ""
        if getattr(session, "washout_pending", False):
            washout_display_value = build_washout_display(session.user_id, session.session_data, session=session)
        
        # 杩斿洖鏇存柊
        return (
            session.chat_history, session,
            format_memory_for_display(session),
            format_program_for_display(session),
            gr.update(visible=not is_chinese),  # title_en
            gr.update(visible=is_chinese),      # title_zh
            gr.update(visible=not is_chinese),  # intro_en
            gr.update(visible=is_chinese),      # intro_zh
            gr.update(visible=not is_chinese),  # study_instructions_en
            gr.update(visible=is_chinese),      # study_instructions_zh
            gr.update(visible=not is_chinese),  # questionnaire_instruction_en
            gr.update(visible=is_chinese),      # questionnaire_instruction_zh
            gr.update(visible=not is_chinese),  # program_title_en
            gr.update(visible=is_chinese),      # program_title_zh
            gr.update(label=new_texts["participant_id_label"], placeholder=new_texts["participant_id_placeholder"]),
            gr.update(value=new_texts["start_session"]),
            gr.update(label=new_texts["audio_label"]),
            chat_input_update,  # msg_input
            gr.update(value=ui_text["memory"]),    # toggle_memory_btn
            submit_update,    # submit_btn
            gr.update(value=new_texts["finish_session"], interactive=bool(getattr(session, "session_completed", False))),   # finish_session_btn
            gr.update(value=ui_text["program"]),   # toggle_program_btn
            gr.update(value=ui_text["clear"]),     # clear_btn
            gr.update(value=ui_text["save"]),      # save_btn
            gr.update(label=ui_text["save_status"]), # save_info
            *get_sam_ui_updates(session),
            *get_panas_ui_updates(session),
            gr.update(value=new_texts["sam_submit"]),   # sam_submit_btn
            gr.update(value=new_texts["panas_submit"]),  # panas_submit_btn
            *get_sus_ui_updates(session),
            gr.update(value=new_texts["sus_submit"]),   # sus_submit_btn
            *get_therapy_ui_updates(session),
            gr.update(value=new_texts["therapy_submit"]),  # therapy_submit_btn
            *get_experiment_screen_updates(session),
            gr.update(value=build_participant_status_message(session)),
            gr.update(value=washout_display_value, visible=bool(washout_display_value))
        )
    
    # 鍒濆鍖栧簲鐢?    demo.load(
        initialize_session,
        outputs=[
            session_state, user_id_input, participant_status, washout_display,
            study_entry, therapy_workspace, final_completion_display,
            *([washout_timer] if washout_timer else []),
            chatbot, user_memory_display, program_info_display, audio_player,
            sam_panel, sam_title, sam_instruction, sam_valence, sam_arousal, sam_status,
            panas_panel, panas_title, *panas_item_components, panas_status,
            msg_input, submit_btn, finish_session_btn,
            sus_panel, sus_title, *sus_item_components, sus_status,
            therapy_panel, therapy_title, *therapy_item_components, therapy_status
        ]
    )

    start_session_btn.click(
        start_participant_session,
        [user_id_input, session_state],
        [
            session_state, user_id_input, chatbot, user_memory_display, program_info_display, participant_status, washout_display, audio_player,
            study_entry, therapy_workspace, final_completion_display,
            *([washout_timer] if washout_timer else []),
            sam_panel, sam_title, sam_instruction, sam_valence, sam_arousal, sam_status,
            panas_panel, panas_title, *panas_item_components, panas_status,
            msg_input, submit_btn, finish_session_btn,
            sus_panel, sus_title, *sus_item_components, sus_status,
            therapy_panel, therapy_title, *therapy_item_components, therapy_status
        ]
    )
    
    # 鎶藉眽鍒囨崲浜嬩欢
    toggle_memory_btn.click(
        toggle_drawer,
        inputs=[left_drawer_visible],
        outputs=[left_drawer]
    ).then(
        lambda x: not x,
        inputs=[left_drawer_visible],
        outputs=[left_drawer_visible]
    )
    
    toggle_program_btn.click(
        toggle_drawer,
        inputs=[right_drawer_visible],
        outputs=[right_drawer]
    ).then(
        lambda x: not x,
        inputs=[right_drawer_visible],
        outputs=[right_drawer_visible]
    )
    
    # 鎻愪氦鎸夐挳浜嬩欢閾?    submit_btn.click(
        process_dialogue_stream,
        [msg_input, session_state],
        [chatbot, session_state, finish_session_btn]
    ).then(
        lambda: "",
        None,
        [msg_input]  # 娓呯┖杈撳叆妗?    ).then(
        generate_music_stream,
        [session_state],
        [
            chatbot, session_state, user_memory_display, program_info_display, progress_display, audio_player,
            sam_panel, sam_title, sam_instruction, sam_valence, sam_arousal, sam_status,
            panas_panel, panas_title, *panas_item_components, panas_status,
            msg_input, submit_btn,
            finish_session_btn
        ]
    )
    
    # 杈撳叆妗嗗洖杞︽彁浜?    msg_input.submit(
        process_dialogue_stream,
        [msg_input, session_state],
        [chatbot, session_state, finish_session_btn]
    ).then(
        lambda: "",
        None,
        [msg_input]  # 娓呯┖杈撳叆妗?    ).then(
        generate_music_stream,
        [session_state],
        [
            chatbot, session_state, user_memory_display, program_info_display, progress_display, audio_player,
            sam_panel, sam_title, sam_instruction, sam_valence, sam_arousal, sam_status,
            panas_panel, panas_title, *panas_item_components, panas_status,
            msg_input, submit_btn,
            finish_session_btn
        ]
    )
    
    # 娓呴櫎瀵硅瘽鎸夐挳
    clear_btn.click(
        clear_conversation,
        [session_state],
        [
            chatbot, session_state, user_memory_display, program_info_display, audio_player,
            study_entry, therapy_workspace, final_completion_display,
            *([washout_timer] if washout_timer else []),
            sam_panel, sam_title, sam_instruction, sam_valence, sam_arousal, sam_status,
            panas_panel, panas_title, *panas_item_components, panas_status,
            msg_input, submit_btn, finish_session_btn,
            sus_panel, sus_title, *sus_item_components, sus_status,
            therapy_panel, therapy_title, *therapy_item_components, therapy_status
        ]
    )
    
    # 淇濆瓨浼氳瘽鎸夐挳
    save_btn.click(
        save_session,
        [session_state],
        [save_info]
    )

    
    # 璇█鍒囨崲浜嬩欢
    language_radio.change(
        change_language,
        inputs=[language_radio, session_state],
        outputs=[
            chatbot, session_state, user_memory_display, program_info_display,
            title_en, title_zh, intro_en, intro_zh,
            study_instructions_en, study_instructions_zh,
            questionnaire_instruction_en, questionnaire_instruction_zh,
            program_title_en, program_title_zh,
            user_id_input, start_session_btn, audio_player,
            msg_input, toggle_memory_btn, submit_btn, finish_session_btn, toggle_program_btn,
            clear_btn, save_btn, save_info,
            sam_panel, sam_title, sam_instruction, sam_valence, sam_arousal, sam_status,
            panas_panel, panas_title, *panas_item_components, panas_status,
            sam_submit_btn, panas_submit_btn,
            sus_panel, sus_title, *sus_item_components, sus_status, sus_submit_btn,
            therapy_panel, therapy_title, *therapy_item_components, therapy_status, therapy_submit_btn,
            study_entry, therapy_workspace, final_completion_display,
            participant_status, washout_display
        ]
    )

    if washout_timer and hasattr(washout_timer, "tick"):
        washout_timer.tick(
            refresh_washout_screen,
            [session_state],
            [washout_display, start_session_btn, study_entry, washout_timer]
        )

    sam_submit_btn.click(
        submit_sam_rating,
        [session_state, sam_valence, sam_arousal],
        [
            session_state,
            sam_panel, sam_title, sam_instruction, sam_valence, sam_arousal, sam_status,
            panas_panel, panas_title, *panas_item_components, panas_status
        ]
    )

    panas_submit_btn.click(
        submit_panas_rating,
        [session_state, *panas_item_components],
        [
            session_state,
            panas_panel, panas_title, *panas_item_components, panas_status,
            msg_input, submit_btn,
            sus_panel, sus_title, *sus_item_components, sus_status,
            therapy_panel, therapy_title, *therapy_item_components, therapy_status,
            participant_status, washout_display, user_id_input,
            study_entry, therapy_workspace, final_completion_display,
            *([washout_timer] if washout_timer else [])
        ]
    )

    finish_session_btn.click(
        finish_session,
        [session_state],
        [
            chatbot, session_state,
            sam_panel, sam_title, sam_instruction, sam_valence, sam_arousal, sam_status,
            panas_panel, panas_title, *panas_item_components, panas_status,
            msg_input, submit_btn,
            sus_panel, sus_title, *sus_item_components, sus_status,
            therapy_panel, therapy_title, *therapy_item_components, therapy_status
        ]
    )

    sus_submit_btn.click(
        submit_sus_rating,
        [session_state, *sus_item_components],
        [
            session_state,
            sus_panel, sus_title, *sus_item_components, sus_status,
            therapy_panel, therapy_title, *therapy_item_components, therapy_status
        ]
    )

    therapy_submit_btn.click(
        submit_therapy_experience_rating,
        [session_state, *therapy_item_components],
        [
            session_state,
            therapy_panel, therapy_title, *therapy_item_components, therapy_status,
            study_entry, therapy_workspace, final_completion_display,
            participant_status, washout_display,
            *([washout_timer] if washout_timer else [])
        ]
    )

if __name__ == "__main__":
    try:
        # 娣诲姞蹇呰鐨勫弬鏁颁互鏀寔鏂囦欢浼犺緭
        demo.launch(
            share=True,
            server_name="0.0.0.0",  # Server/public launch
            server_port=7860,       # Fixed port for deployment
            show_error=True,        # 鏄剧ず璇︾粏閿欒淇℃伅
            show_api=False,
            quiet=False,            # 鏄剧ず璇︾粏鏃ュ織
            allowed_paths=[
                os.path.abspath("output"),
                os.path.abspath("output/kimusic_generated"),
                os.path.abspath("../toy_dataset/mp3")
            ]
        )
    except Exception as e:
        print(f"鍚姩搴旂敤澶辫触: {e}")
    finally:
        # 娓呯悊宸ヤ綔鍦ㄨ繖閲屼笉闇€瑕佺壒瀹氱殑therapy_session瀹炰緥
        print("搴旂敤宸插叧闂?) 



