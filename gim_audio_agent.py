"""
GIM闊抽缂栬緫浠ｇ悊绯荤粺
瀹炵幇鍩轰簬瀵硅瘽鐨勬櫤鑳介煶涔愮紪杈戝拰鍚堟垚鍔熻兘
"""
from __future__ import annotations


import os
import json
import re
import http.client
import time
import socket
import subprocess
import tempfile
import math
import asyncio
import concurrent.futures
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
import base64
from concurrent.futures import ThreadPoolExecutor

from pydub import AudioSegment
from pydub.effects import speedup
from pydub.utils import which

from hyper_parameters import MODEL_NAME,MUSIC_MODEL_NAME, MAX_TOKENS_RESPONSE, CANDIDATES_PER_PHASE


@dataclass
class ProgramPhase:
    phase: str
    duration_seconds: int
    purpose: str
    search_criteria: Dict[str, Any]


@dataclass
class MusicCandidate:
    """闊充箰鍊欓€夐」"""
    filename: str
    title: str
    duration_seconds: int
    metadata: Dict[str, Any] = None


@dataclass
class MusicSegment:
    """绠€鍗曠殑闊充箰鐗囨锛堝悜鍚庡吋瀹癸級"""
    filename: str
    duration_seconds: int
    title: str = None


@dataclass
class ProcessedMusicSegment:
    """澶勭悊鍚庣殑闊充箰鐗囨"""
    filename: str
    title: str
    start_time_seconds: float  # 浠庡師闊抽鐨勫摢涓椂闂寸偣寮€濮嬫埅鍙?    duration_seconds: float    # 鎴彇鐨勯暱搴?    speed_ratio: float = 1.0   # 閫熷害璋冩暣姣斾緥 (1.0 = 鍘熼€熷害)
    pitch_shift: float = 0.0   # 闊宠皟璋冩暣 (鍗婇煶锛? = 涓嶈皟鏁?
    volume_adjust: float = 1.0 # 闊抽噺璋冩暣 (1.0 = 鍘熼煶閲?
    fade_in_ms: int = 1000     # 娣″叆鏃堕暱
    fade_out_ms: int = 1500    # 娣″嚭鏃堕暱
    processing_reason: str = ""  # 澶勭悊鍘熷洜璇存槑


class GIMAudioAgent:
    """GIM 闊抽缂栬緫浠ｇ悊锛?    1) 瀵硅瘽鐞嗚В涓庢剰鍥惧垎鏋?    2) Program 璁捐
    3) 闊充箰妫€绱笌鍒嗘
    4) 闊抽鍚堟垚涓庣紪杈?    """

    def __init__(self, music_db, api_key: str, music_root: str = "../toy_dataset/mp3", output_dir: str = "output"):
        self.music_db = music_db
        self.api_key = api_key
        self.music_root = music_root
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        # 涓嶅啀澶嶇敤杩炴帴锛岄伩鍏嶈繙绔叧闂繛鎺ラ棶棰?        self.conn = None

    # ------------------------
    # 鍐呴儴宸ュ叿锛歠fmpeg 闊抽澶勭悊
    # ------------------------
    def _ffmpeg_tempo(self, audio: AudioSegment, tempo: float) -> AudioSegment:
        """浣跨敤ffmpeg鐨刟tempo婊ら暅璋冩暣閫熷害锛堜笉鏀瑰彉闊抽珮锛夈€傛敮鎸?.5~2.0锛岃秴鍑鸿寖鍥存媶鍒嗛摼寮忓鐞嗐€?""
        if abs(tempo - 1.0) < 1e-3:
            return audio
        # 鐢熸垚tempo鍥犲瓙閾撅紙姣忎釜鍦?.5~2.0鑼冨洿鍐咃級
        def factors_for_tempo(t: float) -> List[float]:
            factors = []
            if t < 0.5:
                # 杩炵画涔樹互0.5鐩村埌>=0.5
                while t < 0.5:
                    factors.append(0.5)
                    t /= 0.5
                if t != 1.0:
                    factors.append(t)
            elif t > 2.0:
                while t > 2.0:
                    factors.append(2.0)
                    t /= 2.0
                if t != 1.0:
                    factors.append(t)
            else:
                factors.append(t)
            return factors
        factors = factors_for_tempo(tempo)

        # 涓存椂鏂囦欢澶勭悊
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src:
            src_path = src.name
            audio.export(src_path, format="wav")
        current_input = src_path
        try:
            for i, f in enumerate(factors):
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst:
                    dst_path = dst.name
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", current_input,
                    "-filter:a", f"atempo={f}",
                    dst_path
                ]
                subprocess.run(cmd, check=True)
                # 涓嬫杩唬浠st涓鸿緭鍏?                if current_input != src_path and os.path.exists(current_input):
                    try:
                        os.unlink(current_input)
                    except Exception:
                        pass
                current_input = dst_path
            # 鍔犺浇鏈€缁堣緭鍑?            result = AudioSegment.from_file(current_input)
            return result
        finally:
            try:
                if os.path.exists(current_input):
                    os.unlink(current_input)
            except Exception:
                pass
            try:
                if os.path.exists(src_path):
                    os.unlink(src_path)
            except Exception:
                pass

    def _ffmpeg_pitch_shift(self, audio: AudioSegment, semitones: float) -> AudioSegment:
        """浣跨敤ffmpeg瀹炵幇鍗婇煶绉昏皟锛屽悓鏃跺敖閲忎繚鎸佸師鏈夋椂闀匡紙浣跨敤asetrate+atempo鏍℃锛夈€?""
        if abs(semitones) < 1e-3:
            return audio
        factor = 2 ** (semitones / 12.0)
        sr = audio.frame_rate

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src:
            src_path = src.name
            audio.export(src_path, format="wav")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst:
            dst_path = dst.name
        try:
            # 閫氳繃asetrate鏀瑰彉閲囨牱鐜囦粠鑰屾敼鍙橀煶楂橈紝鐒跺悗鐢╝tempo鎭㈠鑺傛媿锛屾渶鍚巃resample鍥炲師閲囨牱鐜?            # 娉ㄦ剰锛歛tempo鑼冨洿涓?.5~2.0锛岃嫢factor瓒呭嚭鑼冨洿锛屽彲浠ュ垎娈靛鐞嗭紝浣嗛€氬父鍗婇煶鍙樺寲鍦ㄦ鑼冨洿鍐?            # 鑻?/factor涓嶅湪0.5~2.0锛屾媶鍒嗛摼
            tempo_corr = 1.0 / factor
            if 0.5 <= tempo_corr <= 2.0:
                filter_str = f"asetrate={int(sr*factor)},atempo={tempo_corr},aresample={sr}"
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", src_path,
                    "-filter:a", filter_str,
                    dst_path
                ]
                subprocess.run(cmd, check=True)
            else:
                # 鍏堟敼鍙橀噰鏍风巼涓庨噸閲囨牱寰楀埌鏀瑰彉闊抽珮鐨勯煶棰戯紙閫熷害涔熷彉浜嗭級锛屽啀鐢ㄩ摼寮廰tempo鎷夊洖
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp1:
                    tmp1_path = tmp1.name
                cmd1 = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", src_path,
                    "-filter:a", f"asetrate={int(sr*factor)},aresample={sr}",
                    tmp1_path
                ]
                subprocess.run(cmd1, check=True)
                # 閾惧紡tempo绾犳
                corrected = self._ffmpeg_tempo(AudioSegment.from_file(tmp1_path), tempo_corr)
                corrected.export(dst_path, format="wav")
                try:
                    os.unlink(tmp1_path)
                except Exception:
                    pass
            result = AudioSegment.from_file(dst_path)
            return result
        finally:
            for p in [src_path, dst_path]:
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass

    # ------------------------
    # Step 1: 瀵硅瘽鐞嗚В涓庢不鐤楁剰鍥惧垎鏋?    # ------------------------
    def analyze_dialogue(self, chat_history: List[Dict[str, str]]) -> Dict[str, Any]:
        system_prompt = (
            "You are a clinical GIM therapist assistant. Read the conversation and extract a JSON with: "
            "current_emotion (one word or short phrase), key_themes (array), therapeutic_goal (string). "
            "Return ONLY JSON, no explanations."
        )

        # 鎷兼帴鏈€鐩稿叧鐨勫璇濆唴瀹癸紙鎺у埗闀垮害锛?        clipped_history = chat_history[-12:] if len(chat_history) > 12 else chat_history
        history_text = "\n".join([f"{m['role']}: {m['content']}" for m in clipped_history])

        user_prompt = (
            "Conversation transcripts (role: content):\n" + history_text +
            "\n\nExtract JSON strictly as: {\n  \"current_emotion\": string,\n  \"key_themes\": [string,...],\n  \"therapeutic_goal\": string\n}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response_text = self._call_llm(MODEL_NAME,messages)
        data = self._extract_json(response_text)
        if not isinstance(data, dict):
            # 鍏滃簳
            data = {
                "current_emotion": "uncertain",
                "key_themes": ["exploration"],
                "therapeutic_goal": "provide safety and calm"
            }
        return data

    # ------------------------
    # Step 2: 璁捐 Program
    # ------------------------
    def design_program(self, analysis: Dict[str, Any]) -> List[ProgramPhase]:
        """浣跨敤LLM璁捐娌荤枟Program锛屽寘鍚洿鏅鸿兘鐨勯煶涔愭悳绱㈡潯浠剁敓鎴?""
        
        # 鑾峰彇闊充箰搴撲腑鍙敤鐨勫叧閿瘝閫夐」锛堝厛璁鹃粯璁わ紝閬垮厤 if 涓嶈繘鍏?try 鏃舵湭瀹氫箟锛?        default_mood = ["calm", "peaceful", "relaxing", "introspective", "contemplative", "uplifting", "energetic", "grounding", "flowing", "balanced"]
        default_genre = ["classical", "ambient", "instrumental", "piano", "orchestral", "electronic", "folk", "jazz", "meditation"]
        default_tag = ["nature", "meditation", "healing", "therapy", "mindfulness", "breathing", "centering", "flowing", "gentle", "soft"]
        default_theme = ["meditation", "therapy", "relaxation", "introspection", "healing", "mindfulness"]
        mood_options = default_mood.copy()
        genre_options = default_genre.copy()
        tag_options = default_tag.copy()
        theme_options = default_theme.copy()
        tempo_options = ["slow", "medium", "fast"]
        dynamics_options = ["soft", "moderate", "intense"]
        
        try:
            if self.music_db and hasattr(self.music_db, 'MusicDocumentManager') and self.music_db.MusicDocumentManager:
                mood_options = self.music_db.MusicDocumentManager.get_attribute_options("mood", 30)
                genre_options = self.music_db.MusicDocumentManager.get_attribute_options("genre", 30)
                # 鑾峰彇鏍囩閫夐」
                tag_options = self.music_db.MusicDocumentManager.get_attribute_options("tags", 50)
                theme_options = self.music_db.MusicDocumentManager.get_attribute_options("theme", 30)
        except Exception as e:
            print(f"鑾峰彇闊充箰搴撻€夐」澶辫触: {e}")
            # 浣跨敤榛樿閫夐」锛堜笂闈㈠凡鍒濆鍖栵紝姝ゅ鍙渷鐣ワ紝淇濈暀浠ユ槑纭涔夛級
            mood_options = default_mood
            genre_options = default_genre
            tag_options = default_tag
            theme_options = default_theme
        
        # 鏋勫缓绯荤粺鎻愮ず璇嶏紝鍖呭惈鍙敤鐨勫叧閿瘝閫夐」
        system_prompt = (
            "You are an expert GIM (Guided Imagery and Music) therapist. "
            "Design a therapeutic program with multiple phases based on the client's needs. "
            "Each phase should have specific music selection criteria that can be matched against the available music library.\n\n"
            "Available music attributes to choose from:\n"
            f"- Moods: {', '.join(mood_options[:20])}\n"
            f"- Genres: {', '.join(genre_options[:20])}\n"
            f"- Tags: {', '.join(tag_options[:20])}\n"
            f"- Themes: {', '.join(theme_options[:20])}\n"
            f"- Tempo: {', '.join(tempo_options)}\n"
            f"- Dynamics: {', '.join(dynamics_options)}\n\n"
            "For each phase, provide search criteria that:\n"
            "1. Use keywords that exist in the available options above\n"
            "2. Are specific enough to find relevant music but not too restrictive\n"
            "3. Include both positive criteria (what to include) and negative criteria (what to avoid)\n"
            "4. Consider the therapeutic progression and emotional flow between phases\n\n"
            "Return a JSON array with each phase containing:\n"
            "- phase: descriptive name\n"
            "- duration_seconds: fixed short demo duration. Use exactly 20, 20, 25, 25 seconds for the four phases, summing to 90 seconds total.\n"
            "- purpose: therapeutic goal\n"
            "- search_criteria: music selection criteria with mood_keywords, genre_keywords, tempo_preference, dynamics_preference, avoid_keywords"
        )
        
        # 鏋勫缓鐢ㄦ埛鎻愮ず璇?        user_prompt = (
            f"Client Analysis:\n"
            f"- Emotional State: {analysis.get('emotional_state', 'unknown')}\n"
            f"- Therapeutic Goals: {analysis.get('therapeutic_goals', 'general wellness')}\n"
            f"- Current Challenges: {analysis.get('current_challenges', 'not specified')}\n"
            f"- Preferred Duration: fixed 90 seconds total for demo/ablation testing\n\n"
            f"Design a GIM program with 3-4 phases that:\n"
            f"1. Addresses the client's emotional state and goals\n"
            f"2. Provides a natural therapeutic progression\n"
            f"3. Uses music that can actually be found in our library\n"
            f"4. Creates a balanced emotional journey\n\n"
            f"Remember to use only keywords that exist in the available options I provided above."
        )
        
        try:
            response = self._call_llm(MODEL_NAME,[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ])
            
            phases_data = self._extract_json(response)
            if not phases_data or not isinstance(phases_data, list):
                print("LLM杩斿洖鐨勬牸寮忔棤鏁堬紝浣跨敤榛樿绋嬪簭")
                print("!!response: ", response)
                return self._get_default_program(analysis)
            
            phases = []
            demo_durations = [20, 20, 25, 25]

            for phase_data in phases_data:
                try:
                    phase_idx = len(phases)

                    phase = ProgramPhase(
                        phase=phase_data.get("phase", "Unknown Phase"),
                        duration_seconds=demo_durations[phase_idx] if phase_idx < len(demo_durations) else 20,
                        purpose=phase_data.get("purpose", "General therapeutic support"),
                        search_criteria=phase_data.get("search_criteria", {})
                    )

                    phases.append(phase)

                except Exception as e:
                    print(f"瑙ｆ瀽闃舵鏁版嵁澶辫触: {e}")
                    continue
            
            if not phases:
                print("娌℃湁鎴愬姛瑙ｆ瀽浠讳綍闃舵锛屼娇鐢ㄩ粯璁ょ▼搴?)
                return self._get_default_program(analysis)
            
            print(f"璁捐浜?{len(phases)} 涓不鐤楅樁娈?)
            for i, phase in enumerate(phases, 1):
                print(f"  {i}. {phase.phase} ({phase.duration_seconds}s): {phase.purpose}")
                print(f"     鎼滅储鏉′欢: {phase.search_criteria}")
            
            return phases
            
        except Exception as e:
            print(f"LLM璋冪敤澶辫触: {e}")
            return self._get_default_program(analysis)
    
    def _get_default_program(self, analysis: Dict[str, Any]) -> List[ProgramPhase]:
        """褰揕LM澶辫触鏃舵彁渚涢粯璁ょ殑娌荤枟绋嬪簭"""
        raise NotImplementedError("榛樿绋嬪簭鏈疄鐜?)
        emotional_state = analysis.get('emotional_state', 'neutral')
        
        # 鍩轰簬鎯呯华鐘舵€佹彁渚涢粯璁ょ▼搴?        if 'overwhelmed' in emotional_state.lower() or 'stressed' in emotional_state.lower():
            return [
                ProgramPhase("1. Grounding", 300, "Establish safety and presence", {
                    "mood_keywords": ["calm", "gentle", "soothing", "grounding"],
                    "tempo_preference": "slow",
                    "dynamics_preference": "soft",
                    "avoid_keywords": ["chaotic", "intense", "dramatic", "urgent"]
                }),
                ProgramPhase("2. Acknowledging Pressure", 300, "Recognize and validate current challenges", {
                    "mood_keywords": ["tension", "weight", "struggle", "building"],
                    "tempo_preference": "medium",
                    "dynamics_preference": "moderate",
                    "avoid_keywords": ["aggressive", "violent", "chaotic"]
                }),
                ProgramPhase("3. Finding Inner Space", 300, "Create internal spaciousness and breathing room", {
                    "mood_keywords": ["spacious", "expansive", "open", "breathing", "flowing"],
                    "tempo_preference": "medium",
                    "dynamics_preference": "moderate",
                    "avoid_keywords": ["constricted", "heavy", "frantic"]
                }),
                ProgramPhase("4. Rhythmic Balance", 300, "Establish healthy internal rhythms and flow", {
                    "mood_keywords": ["rhythmic", "balanced", "flowing", "structured", "playful"],
                    "tempo_preference": "medium",
                    "dynamics_preference": "moderate",
                    "avoid_keywords": ["rigid", "monotonous", "frenzied"]
                }),
                ProgramPhase("5. Integration", 300, "Bring together insights and new patterns", {
                    "mood_keywords": ["integrated", "whole", "harmonious", "peaceful", "complete"],
                    "tempo_preference": "slow",
                    "dynamics_preference": "soft",
                    "avoid_keywords": ["fragmented", "chaotic", "unresolved"]
                })
            ]
        else:
            # 閫氱敤绋嬪簭
            return [
                ProgramPhase("1. Opening", 300, "Create safe therapeutic space", {
                    "mood_keywords": ["calm", "welcoming", "gentle", "peaceful"],
                    "tempo_preference": "slow",
                    "dynamics_preference": "soft",
                    "avoid_keywords": ["intense", "chaotic", "aggressive"]
                }),
                ProgramPhase("2. Exploration", 300, "Deepen into therapeutic focus", {
                    "mood_keywords": ["introspective", "contemplative", "thoughtful", "curious"],
                    "tempo_preference": "medium",
                    "dynamics_preference": "moderate",
                    "avoid_keywords": ["distracting", "overwhelming", "superficial"]
                }),
                ProgramPhase("3. Transformation", 300, "Support positive change and insight", {
                    "mood_keywords": ["flowing", "transforming", "evolving", "expanding"],
                    "tempo_preference": "medium",
                    "dynamics_preference": "moderate",
                    "avoid_keywords": ["stuck", "rigid", "confining"]
                }),
                ProgramPhase("4. Integration", 300, "Consolidate gains and prepare for closure", {
                    "mood_keywords": ["integrated", "whole", "harmonious", "complete"],
                    "tempo_preference": "slow",
                    "dynamics_preference": "soft",
                    "avoid_keywords": ["fragmented", "unresolved", "abrupt"]
                })
            ]

    # ------------------------
    # Step 3: 闊充箰妫€绱笌鍊欓€夐」鏀堕泦
    # ------------------------
    def retrieve_music_candidates(self, phases: List[ProgramPhase], candidates_per_phase: int = 1) -> Dict[str, List[MusicCandidate]]:
        """涓烘瘡涓樁娈垫绱㈠涓煶涔愬€欓€夐」锛屼娇鐢ㄥ绾у洖閫€绛栫暐纭繚姣忎釜闃舵閮芥湁鍊欓€夐」"""
        phase_candidates = {}
        
        for phase in phases:
            criteria = phase.search_criteria or {}
            candidates = []
            
            # 绛栫暐1: 浣跨敤鍘熷鏉′欢杩涜绮剧‘鎼滅储
            candidates = self._search_with_criteria(criteria, candidates_per_phase)
            
            # 绛栫暐2: 濡傛灉绮剧‘鎼滅储澶辫触锛屼娇鐢ㄥ鏉炬潯浠舵悳绱?            if len(candidates) < candidates_per_phase:
                print(f"闃舵 '{phase.phase}' 绮剧‘鎼滅储鍙壘鍒?{len(candidates)} 涓€欓€夐」锛屽皾璇曞鏉炬悳绱?..")
                relaxed_criteria = self._create_relaxed_criteria(criteria)
                additional_candidates = self._search_with_criteria(relaxed_criteria, candidates_per_phase - len(candidates))
                candidates.extend(additional_candidates)
            
            # 绛栫暐3: 濡傛灉浠嶇劧涓嶅锛屼娇鐢ㄩ€氱敤鏉′欢鎼滅储
            if len(candidates) < candidates_per_phase:
                print(f"闃舵 '{phase.phase}' 瀹芥澗鎼滅储鍚庡彧鏈?{len(candidates)} 涓€欓€夐」锛屼娇鐢ㄩ€氱敤鎼滅储...")
                general_criteria = self._create_general_criteria(phase.purpose)
                additional_candidates = self._search_with_criteria(general_criteria, candidates_per_phase - len(candidates))
                candidates.extend(additional_candidates)
            
            # 绛栫暐4: 鏈€鍚庣殑鍥為€€ - 闅忔満閫夋嫨
            if len(candidates) < candidates_per_phase:
                print(f"闃舵 '{phase.phase}' 鎼滅储鍚庡彧鏈?{len(candidates)} 涓€欓€夐」锛屼娇鐢ㄩ殢鏈洪€夋嫨...")
                random_candidates = self._get_random_candidates(candidates_per_phase - len(candidates))
                candidates.extend(random_candidates)
            
            # 楠岃瘉鏂囦欢瀛樺湪鎬у苟鍒涘缓MusicCandidate瀵硅薄
            valid_candidates = []
            for track in candidates:
                filename = track.get("filename", "")
                title = track.get("title", filename)
                
                # 濡傛灉鍙湁鏂囦欢鍚嶏紝鎷煎埌鏍圭洰褰?                if filename:
                    candidate_path = os.path.abspath(
                        os.path.join(os.path.dirname(__file__), "../toy_dataset/mp3", os.path.basename(filename))
                    )
                    if os.path.exists(candidate_path):
                        filename = candidate_path

                if filename and os.path.exists(filename):

                    # 鑾峰彇闊抽鏂囦欢鐨勭湡瀹炴椂闀?                    try:
                        audio = AudioSegment.from_file(filename)
                        actual_duration = len(audio) / 1000.0  # 杞崲涓虹
                    except Exception:
                        actual_duration = track.get("duration", 300)  # 榛樿5鍒嗛挓
                    
                    valid_candidates.append(MusicCandidate(
                        filename=filename,
                        title=title,
                        duration_seconds=actual_duration,
                        metadata=track
                    ))
            
            phase_candidates[phase.phase] = valid_candidates
            print(f"闃舵 '{phase.phase}' 鏈€缁堟壘鍒?{len(valid_candidates)} 涓湁鏁堝€欓€夐」")
        
        return phase_candidates
    
    def _search_with_criteria(self, criteria: Dict[str, Any], num_tracks: int) -> List[Dict[str, Any]]:
        """浣跨敤缁欏畾鏉′欢鎼滅储闊充箰"""
        try:
            tracks = self.music_db.retrieve_music_for_therapy(criteria, num_tracks=num_tracks) or []
            return tracks
        except Exception as e:
            print(f"鎼滅储澶辫触: {e}")
            for k in ["tempo_preference", "dynamics_preference"]:
                val = relaxed_criteria.get(k)
                if isinstance(val, list) and len(val) > 0:
                   relaxed_criteria[k] = val[0]
    
    def _create_relaxed_criteria(self, original_criteria: Dict[str, Any]) -> Dict[str, Any]:
        """鍒涘缓瀹芥澗鐨勬悳绱㈡潯浠?""
        relaxed_criteria = original_criteria.copy()
        #raise NotImplementedError("鍒涘缓瀹芥澗鐨勬悳绱㈡潯浠舵湭瀹炵幇")
        for k in ["tempo_preference", "dynamics_preference"]:
            val = relaxed_criteria.get(k)
            if isinstance(val, list) and len(val) > 0:
                relaxed_criteria[k] = val[0]
        
        # 鏀惧tempo闄愬埗
        if "tempo_preference" in relaxed_criteria:
            tempo_mapping = {
                "slow": "medium",      # 鎱㈤€?-> 涓€?                "medium": "medium",    # 涓€熶繚鎸佷笉鍙?                "fast": "medium"       # 蹇€?-> 涓€?            }
            relaxed_criteria["tempo_preference"] = tempo_mapping.get(
                relaxed_criteria["tempo_preference"], "medium"
            )
        
        # 鏀惧dynamics闄愬埗
        if "dynamics_preference" in relaxed_criteria:
            dynamics_mapping = {
                "soft": "moderate",     # 鏌斿拰 -> 涓瓑
                "moderate": "moderate", # 涓瓑淇濇寔涓嶅彉
                "intense": "moderate"   # 寮虹儓 -> 涓瓑
            }
            relaxed_criteria["dynamics_preference"] = dynamics_mapping.get(
                relaxed_criteria["dynamics_preference"], "moderate"
            )
        
        # 鍑忓皯mood鍏抽敭璇嶏紝鍙繚鐣欐渶鏍稿績鐨?        if "mood_keywords" in relaxed_criteria and relaxed_criteria["mood_keywords"]:
            # 淇濈暀鍓?涓叧閿瘝
            relaxed_criteria["mood_keywords"] = relaxed_criteria["mood_keywords"][:2]
        
        # 鍑忓皯閬垮厤鍏抽敭璇?        if "avoid_keywords" in relaxed_criteria and relaxed_criteria["avoid_keywords"]:
            # 鍙繚鐣欐渶鍏抽敭鐨勯伩鍏嶈瘝
            relaxed_criteria["avoid_keywords"] = relaxed_criteria["avoid_keywords"][:1]
        
        return relaxed_criteria
    
    def _create_general_criteria(self, purpose: str) -> Dict[str, Any]:
        return {
            "mood_keywords": [
                "calm", "soft", "relaxing", "peaceful", "dreamy",
                "hopeful", "bright", "classical", "piano", "background"
            ],
            "genre_keywords": [
                "modern classical", "solo piano", "classical", "instrumental"
            ],
            "tempo_preference": "medium",
            "dynamics_preference": "moderate",
            "avoid_keywords": []
    }
    
    def _get_random_candidates(self, num_tracks: int) -> List[Dict[str, Any]]:
        """鑾峰彇闅忔満闊充箰鍊欓€夐」"""
        raise NotImplementedError("鑾峰彇闅忔満闊充箰鍊欓€夐」鏈疄鐜?)
        try:
            # 浣跨敤閫氱敤鏉′欢鑾峰彇闅忔満闊充箰
            general_criteria = {
                "mood_keywords": ["calm", "peaceful"],
                "tempo_preference": "medium",
                "dynamics_preference": "moderate",
                "avoid_keywords": []
            }
            
            # 灏濊瘯鑾峰彇鏇村鍊欓€夐」锛岀劧鍚庨殢鏈洪€夋嫨
            all_tracks = self.music_db.retrieve_music_for_therapy(general_criteria, num_tracks=20) or []
            
            # 闅忔満閫夋嫨鎸囧畾鏁伴噺鐨勫€欓€夐」
            import random
            if len(all_tracks) > num_tracks:
                return random.sample(all_tracks, num_tracks)
            else:
                return all_tracks
                
        except Exception as e:
            print(f"闅忔満閫夋嫨澶辫触: {e}")
            # 杩斿洖绌哄垪琛紝璁╀笂灞傚鐞?            return []

    def _encode_base64_content_from_file(self, file_path: str) -> str:
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")


    def _get_audio_format(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        mapping = {
            ".mp3": "mp3",
            ".wav": "wav",
            ".flac": "flac",
            ".m4a": "m4a",
            ".ogg": "ogg",
            ".aac": "aac",
        }
        return mapping.get(ext, "mp3")

    # ------------------------
    # Step 4: 鏅鸿兘闊充箰閫夋嫨涓庡鐞嗚璁?    # ------------------------
    def design_music_processing_parallel(self, phases: List[ProgramPhase],
                                      phase_candidates: Dict[str, List[MusicCandidate]],
                                      progress_callback: Optional[callable] = None) -> List[ProcessedMusicSegment]:
        """浣跨敤骞惰LLM璋冪敤涓烘瘡涓樁娈垫櫤鑳介€夋嫨闊充箰骞惰璁″鐞嗘柟妗?        
        杩欐槸design_music_processing鐨勫苟琛屼紭鍖栫増鏈紝閫氳繃骞惰澶勭悊澶氫釜闃舵鏉ュ噺灏戞€讳綋寤惰繜銆?        濡傛灉骞惰澶勭悊澶辫触锛屼細鍥為€€鍒颁覆琛岀増鏈€?        """
        try:
            # 鍒涘缓绾跨▼姹?            with ThreadPoolExecutor(max_workers=min(4, len(phases))) as executor:
                # 鍑嗗鎵€鏈夐樁娈电殑澶勭悊浠诲姟
                future_to_phase = {}
                
                # 棰勫姞杞芥墍鏈夐渶瑕佺殑闊抽鏂囦欢
                audio_cache = {}
                for phase_name, candidates in phase_candidates.items():
                    if candidates:
                        filepath = candidates[0].filename
                        if filepath and os.path.exists(filepath):
                            try:
                                audio_format = self._get_audio_format(filepath)
                                audio_b64 = self._encode_base64_content_from_file(filepath)
                                audio_cache[phase_name] = (audio_format, audio_b64)
                            except Exception as e:
                                print(f"棰勫姞杞介煶棰戞枃浠跺け璐?{filepath}: {e}")
                
                # 姹囨€绘墍鏈夐樁娈电畝浠嬬敤浜庡叏灞€瑙勫垝
                phases_overview_lines = []
                for idx, ph in enumerate(phases, 1):
                    phases_overview_lines.append(
                        f"{idx}. {ph.phase} | duration={ph.duration_seconds}s | purpose={ph.purpose} | criteria={json.dumps(ph.search_criteria, ensure_ascii=False)}"
                    )
                phases_overview = "\n".join(phases_overview_lines)
                
                # 鎻愪氦鎵€鏈夊鐞嗕换鍔?                for phase in phases:
                    candidates = phase_candidates.get(phase.phase, [])
                    if not candidates:
                        continue
                        
                    # 鍑嗗浠诲姟鍙傛暟
                    task_args = (
                        phase,
                        candidates,
                        phases_overview,
                        audio_cache.get(phase.phase)
                    )
                    
                    # 鎻愪氦浠诲姟鍒扮嚎绋嬫睜
                    future = executor.submit(
                        self._process_single_phase,
                        *task_args
                    )
                    future_to_phase[future] = phase
                
                # 鏀堕泦鎵€鏈夌粨鏋?                processed_segments = []
                for future in concurrent.futures.as_completed(future_to_phase):
                    phase = future_to_phase[future]
                    try:
                        segment = future.result()
                        if segment:
                            processed_segments.append(segment)
                            if progress_callback:
                                progress_callback(phase.phase, "澶勭悊瀹屾垚", True)
                            print(f"闃舵 '{phase.phase}' 澶勭悊瀹屾垚")
                    except Exception as e:
                        print(f"澶勭悊闃舵 '{phase.phase}' 澶辫触: {e}")
                        # 璁板綍閿欒浣嗙户缁鐞嗗叾浠栭樁娈?                        continue
                
                return processed_segments
                
        except Exception as e:
            print(f"骞惰澶勭悊澶辫触锛屽洖閫€鍒颁覆琛岀増鏈? {e}")
            # 鍥為€€鍒板師濮嬩覆琛岀増鏈?            return self.design_music_processing(phases, phase_candidates)
    
    def _process_single_phase(self, phase: ProgramPhase, 
                            candidates: List[MusicCandidate],
                            phases_overview: str,
                            audio_data: Optional[Tuple[str, str]] = None) -> Optional[ProcessedMusicSegment]:
        """澶勭悊鍗曚釜闃舵鐨勯煶涔愯璁★紙渚涘苟琛屽鐞嗕娇鐢級"""
        try:
            # 鏋勫缓LLM鎻愮ず璇?            system_prompt = (
                "You are an expert in GIM therapy and audio processing. Given a therapy phase and music candidates, select the best segment and design appropriate audio processing parameters. Output ONLY a valid JSON that strictly matches this schema (no extra text, no comments, no trailing commas):\n\n"
                "{\n"
                "  \"start_time_seconds\": <float, seconds from beginning, >= 0>,\n"
                "  \"duration_seconds\": <float, seconds, > 0>,\n"
                "  \"speed_ratio\": <float, 1.0=normal, 0.8=20% slower, 1.2=20% faster>,\n"
                "  \"pitch_shift\": <int, semitones, 0=no change, +2=2 semitones higher, -1=1 semitone lower>,\n"
                "  \"volume_adjust\": <float, 1.0=normal, 1.5=50% louder, 0.7=30% quieter>,\n"
                "  \"fade_in_ms\": <int, milliseconds, typically 1000-5000, 0 if not needed>,\n"
                "  \"fade_out_ms\": <int, milliseconds, typically 1000-5000, 0 if not needed>,\n"
                "  \"processing_reason\": <string, concise rationale for choices>\n"
                "}\n\n"
                "Rules:\n"
                "- Return JSON only. Do not include explanations or any additional text outside the JSON.\n"
                "- Choose a musically coherent segment that supports therapeutic continuity and smooth transitions.\n"
                "- Avoid unnecessary edits (use speed_ratio=1.0, pitch_shift=0, volume_adjust=1.0 if already appropriate)."
            )
            
            # 鍑嗗鍊欓€夐煶涔愪俊鎭?            candidates_info = []
            for i, candidate in enumerate(candidates):
                temp_metadata = {}
                temp_metadata["filename"] = candidate.filename
                temp_metadata["tags"] = candidate.metadata.get("tags", [])
                temp_metadata["genre"] = candidate.metadata.get("genre", [])
                temp_metadata["mood"] = candidate.metadata.get("mood", [])
                temp_metadata["movement"] = candidate.metadata.get("movement", [])
                temp_metadata["theme"] = candidate.metadata.get("theme", [])
                temp_metadata["tempo"] = candidate.metadata.get("tempo", [])
                temp_metadata["dynamics_rmse_mean"] = candidate.metadata.get("dynamics_rmse_mean", [])

                info = {
                    "index": i,
                    "title": candidate.title,
                    "duration": candidate.duration_seconds,
                    "metadata": temp_metadata or {}
                }
                candidates_info.append(info)
            
            user_prompt = (
                "All Phases Overview (for better global planning and transitions):\n" +
                phases_overview +
                "\n\n" +
                f"Current Phase: {phase.phase}\n" +
                f"Required duration: {phase.duration_seconds} seconds\n" +
                f"Music candidate information:\n{json.dumps(candidates_info, ensure_ascii=False, indent=2)}\n\n" +
                "Instructions:\n"
                "- Select the most suitable segment from a candidate to support therapeutic continuity and smooth transitions.\n"
                "- Output ONLY the JSON defined in the system message. No extra text.\n"
                "- If the original segment already fits well, prefer keeping speed_ratio=1.0, pitch_shift=0, volume_adjust=1.0.\n"
            )

            # 鍑嗗娑堟伅
            messages = [
                {"role": "system", "content": system_prompt}
            ]
            
            # 濡傛灉鏈夐煶棰戞暟鎹紝娣诲姞鍒版秷鎭腑
            if audio_data:
                audio_format, audio_b64 = audio_data
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_b64,
                                "format": audio_format,
                            },
                        },
                        {"type": "text", "text": user_prompt},
                    ],
                })
            else:
                messages.append({"role": "user", "content": user_prompt})
            
            # 璋冪敤LLM锛堥煶棰戞ā鍨嬮渶瑕佹洿闀跨殑澶勭悊鏃堕棿锛?            response_text = self._call_llm(MUSIC_MODEL_NAME, messages, timeout=180)  # 3鍒嗛挓瓒呮椂
            if not response_text:
                print(f"闊抽澶勭悊闃舵 {phase.phase} 瓒呮椂鎴栧け璐ワ紝浣跨敤榛樿鍙傛暟")
                processing_design = None
            else:
                processing_design = self._extract_json(response_text)
            
            if not processing_design or not isinstance(processing_design, dict):
                # 鍏滃簳锛氶€夋嫨绗竴涓€欓€夐」锛屼娇鐢ㄩ粯璁ゅ弬鏁?                selected_candidate = candidates[0]
                processing_design = {
                    "start_time_seconds": 0.0,
                    "duration_seconds": min(phase.duration_seconds, selected_candidate.duration_seconds),
                    "speed_ratio": 1.0,
                    "pitch_shift": 0.0,
                    "volume_adjust": 1.0,
                    "fade_in_ms": 1000,
                    "fade_out_ms": 1500,
                    "processing_reason": "Default selection due to LLM processing failure"
                }
            
            # 楠岃瘉骞跺簲鐢ㄩ€夋嫨
            selected_candidate = candidates[0]  # 褰撳墠瀹炵幇鍙娇鐢ㄧ涓€涓€欓€夐」
            
            # 纭繚鍙傛暟鍚堢悊鎬?            start_time = max(0, float(processing_design.get("start_time_seconds", 0)))
            duration = min(
                phase.duration_seconds,
                float(processing_design.get("duration_seconds", phase.duration_seconds)),
                selected_candidate.duration_seconds - start_time
            )
            
            # 鍙傛暟鑼冨洿楠岃瘉鍜岄粯璁ゅ€艰缃?            speed_ratio = float(processing_design.get("speed_ratio", 1.0))
            pitch_shift = float(processing_design.get("pitch_shift", 0.0))
            volume_adjust = float(processing_design.get("volume_adjust", 1.0))
            fade_in_ms = int(processing_design.get("fade_in_ms", 1000))
            fade_out_ms = int(processing_design.get("fade_out_ms", 1500))
            
            processed_segment = ProcessedMusicSegment(
                filename=selected_candidate.filename,
                title=selected_candidate.title,
                start_time_seconds=start_time,
                duration_seconds=duration,
                speed_ratio=speed_ratio,  # 鍦ㄩ煶棰戝鐞嗘椂鍐嶈繘琛岃寖鍥撮檺鍒?                pitch_shift=pitch_shift,   # 鍦ㄩ煶棰戝鐞嗘椂鍐嶈繘琛岃寖鍥撮檺鍒?                volume_adjust=volume_adjust, # 鍦ㄩ煶棰戝鐞嗘椂鍐嶈繘琛岃寖鍥撮檺鍒?                fade_in_ms=max(0, min(10000, fade_in_ms)),  # 闄愬埗鍦?-10绉?                fade_out_ms=max(0, min(10000, fade_out_ms)), # 闄愬埗鍦?-10绉?                processing_reason=str(processing_design.get("processing_reason", ""))
            )
            
            return processed_segment
            
        except Exception as e:
            print(f"澶勭悊闃舵 {phase.phase} 鏃跺彂鐢熼敊璇? {e}")
            return None

    def design_music_processing(self, phases: List[ProgramPhase], 
                               phase_candidates: Dict[str, List[MusicCandidate]]) -> List[ProcessedMusicSegment]:
        raise NotImplementedError("design_music_processing is not implemented")
        """浣跨敤LLM涓烘瘡涓樁娈垫櫤鑳介€夋嫨闊充箰骞惰璁″鐞嗘柟妗堬紙涓茶鐗堟湰锛屼綔涓哄洖閫€锛?""
        processed_segments = []
        
        # 姹囨€绘墍鏈夐樁娈电畝浠嬶紝甯姪妯″瀷鍏ㄥ眬鎶婃彙涓庤繃娓?        phases_overview_lines = []
        for idx, ph in enumerate(phases, 1):
            phases_overview_lines.append(
                f"{idx}. {ph.phase} | duration={ph.duration_seconds}s | purpose={ph.purpose} | criteria={json.dumps(ph.search_criteria, ensure_ascii=False)}"
            )
        phases_overview = "\n".join(phases_overview_lines)
        
        for phase in phases:
            candidates = phase_candidates.get(phase.phase, [])
            if not candidates:
                continue
                
            # 鏋勫缓LLM鎻愮ず璇嶏紝璁╁叾閫夋嫨鏈€浣抽煶涔愬苟璁捐澶勭悊鏂规
            system_prompt = (
                "You are an expert in GIM therapy and audio processing. Given a therapy phase and music candidates, select the best segment and design appropriate audio processing parameters. Output ONLY a valid JSON that strictly matches this schema (no extra text, no comments, no trailing commas):\n\n"
                "{\n"
                "  \"start_time_seconds\": <float, seconds from beginning, >= 0>,\n"
                "  \"duration_seconds\": <float, seconds, > 0>,\n"
                "  \"speed_ratio\": <float, 1.0=normal, 0.8=20% slower, 1.2=20% faster>,\n"
                "  \"pitch_shift\": <int, semitones, 0=no change, +2=2 semitones higher, -1=1 semitone lower>,\n"
                "  \"volume_adjust\": <float, 1.0=normal, 1.5=50% louder, 0.7=30% quieter>,\n"
                "  \"fade_in_ms\": <int, milliseconds, typically 1000-5000, 0 if not needed>,\n"
                "  \"fade_out_ms\": <int, milliseconds, typically 1000-5000, 0 if not needed>,\n"
                "  \"processing_reason\": <string, concise rationale for choices>\n"
                "}\n\n"
                "Rules:\n"
                "- Return JSON only. Do not include explanations or any additional text outside the JSON.\n"
                "- Choose a musically coherent segment that supports therapeutic continuity and smooth transitions.\n"
                "- Avoid unnecessary edits (use speed_ratio=1.0, pitch_shift=0, volume_adjust=1.0 if already appropriate)."
            )
            
            # 鍑嗗鍊欓€夐煶涔愪俊鎭?            candidates_info = []
            for i, candidate in enumerate(candidates):
                temp_metadata = {}
                #鍙繚瀛榝ilename, tags, genre锛宮ood锛宮ovement锛宼heme锛?audio features涓殑tempo dynamics_rmse_mean
                temp_metadata["filename"] = candidate.filename
                temp_metadata["tags"] = candidate.metadata.get("tags", [])
                temp_metadata["genre"] = candidate.metadata.get("genre", [])
                temp_metadata["mood"] = candidate.metadata.get("mood", [])
                temp_metadata["movement"] = candidate.metadata.get("movement", [])
                temp_metadata["theme"] = candidate.metadata.get("theme", [])
                temp_metadata["tempo"] = candidate.metadata.get("tempo", [])
                temp_metadata["dynamics_rmse_mean"] = candidate.metadata.get("dynamics_rmse_mean", [])

                info = {
                    "index": i,
                    "title": candidate.title,
                    "duration": candidate.duration_seconds,
                    "metadata": temp_metadata or {}
                }
                candidates_info.append(info)
            ##todo锛氫紭鍖杣ser_prompt锛屽彲浠ユ斁鍏ユ墍鏈夌殑phase绠€浠嬶紝甯姪鏇村ソ鐨勮繃娓?            user_prompt = (
                "All Phases Overview (for better global planning and transitions):\n" +
                phases_overview +
                "\n\n" +
                f"Current Phase: {phase.phase}\n" +
                f"Required duration: {phase.duration_seconds} seconds\n" +
                f"Music candidate information:\n{json.dumps(candidates_info, ensure_ascii=False, indent=2)}\n\n" +
                "Instructions:\n"
                "- Select the most suitable segment from a candidate to support therapeutic continuity and smooth transitions.\n"
                "- Output ONLY the JSON defined in the system message. No extra text.\n"
                "- If the original segment already fits well, prefer keeping speed_ratio=1.0, pitch_shift=0, volume_adjust=1.0.\n"
            )

            ###prepare the music input
            filepath = candidates[0].filename
            audio_format = self._get_audio_format(filepath)
            audio_b64 = self._encode_base64_content_from_file(filepath)
            
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_b64,
                                "format": audio_format,
                            },
                        },
                        {"type": "text", "text": user_prompt},
                    ],
                }
            ]
            
            print("!!music messages: ", system_prompt)
            print("!!music user_prompt: ", user_prompt)
            response_text = self._call_llm(MUSIC_MODEL_NAME, messages)
            processing_design = self._extract_json(response_text)
            
            if not processing_design or not isinstance(processing_design, dict):
                # 鍏滃簳锛氶€夋嫨绗竴涓€欓€夐」锛屼娇鐢ㄩ粯璁ゅ弬鏁?                selected_candidate = candidates[0]
                processing_design = {
                    "selected_index": 0,
                    "start_time": 0.0,
                    "duration": min(phase.duration_seconds, selected_candidate.duration_seconds),
                    "speed_ratio": 1.0,
                    "pitch_shift": 0.0,
                    "volume_adjust": 1.0,
                    "fade_in_ms": 1000,
                    "fade_out_ms": 1500,
                    "processing_reason": "Default selection due to LLM processing failure"
                }
            
            # 楠岃瘉骞跺簲鐢ㄩ€夋嫨
            selected_index = processing_design.get("selected_index", 0)
            if 0 <= selected_index < len(candidates):
                selected_candidate = candidates[selected_index]
                
                # 纭繚鍙傛暟鍚堢悊鎬?                start_time = max(0, float(processing_design.get("start_time", 0)))
                duration = min(
                    phase.duration_seconds,
                    float(processing_design.get("duration", phase.duration_seconds)),
                    selected_candidate.duration_seconds - start_time
                )
                
                # 鍙傛暟鑼冨洿楠岃瘉鍜岄粯璁ゅ€艰缃?                speed_ratio = float(processing_design.get("speed_ratio", 1.0))
                pitch_shift = float(processing_design.get("pitch_shift", 0.0))
                volume_adjust = float(processing_design.get("volume_adjust", 1.0))
                fade_in_ms = int(processing_design.get("fade_in_ms", 1000))
                fade_out_ms = int(processing_design.get("fade_out_ms", 1500))
                
                processed_segment = ProcessedMusicSegment(
                    filename=selected_candidate.filename,
                    title=selected_candidate.title,
                    start_time_seconds=start_time,
                    duration_seconds=duration,
                    speed_ratio=speed_ratio,  # 鍦ㄩ煶棰戝鐞嗘椂鍐嶈繘琛岃寖鍥撮檺鍒?                    pitch_shift=pitch_shift,   # 鍦ㄩ煶棰戝鐞嗘椂鍐嶈繘琛岃寖鍥撮檺鍒?                    volume_adjust=volume_adjust, # 鍦ㄩ煶棰戝鐞嗘椂鍐嶈繘琛岃寖鍥撮檺鍒?                    fade_in_ms=max(0, min(10000, fade_in_ms)),  # 闄愬埗鍦?-10绉?                    fade_out_ms=max(0, min(10000, fade_out_ms)), # 闄愬埗鍦?-10绉?                    processing_reason=str(processing_design.get("processing_reason", ""))
                )
                
                processed_segments.append(processed_segment)
                print(f"闃舵 '{phase.phase}' 閫夋嫨闊充箰: {selected_candidate.title}")
                print(f"澶勭悊鏂规: {processed_segment.processing_reason}")
        
        return processed_segments

    # ------------------------
    # Step 5: 楂樼骇闊抽鍚堟垚涓庣紪杈?    # ------------------------
    def synthesize_advanced_audio(self, processed_segments: List[ProcessedMusicSegment], 
                                 output_filename: str = "gim_program_mix.mp3",
                                 crossfade_ms: int = 2000) -> Tuple[str, int]:
        """楂樼骇闊抽鍚堟垚锛屾敮鎸佺簿缁嗗寲鐨勯煶棰戝鐞?""
        if not which("ffmpeg") and not which("ffmpeg.exe"):
            raise RuntimeError("闇€瑕佺郴缁熷凡瀹夎 ffmpeg 鎵嶈兘璇诲彇鍜屽鍑?MP3銆傝鍏堝畨瑁?ffmpeg 骞剁‘淇濆湪 PATH 涓€?)

        compiled: AudioSegment = None
        total_ms = 0
        processing_log = []

        for idx, seg in enumerate(processed_segments):
            if not seg.filename or not os.path.exists(seg.filename):
                print(f"璺宠繃涓嶅瓨鍦ㄧ殑鏂囦欢: {seg.filename}")
                continue

            try:
                # 1. 鍔犺浇闊抽鏂囦欢
                audio = AudioSegment.from_file(seg.filename)
                original_duration = len(audio) / 1000.0
                
                print(f"澶勭悊闃舵 {idx+1}: {seg.title}")
                print(f"  鍘熷鏃堕暱: {original_duration:.1f}s")
                
                # 2. 鎴彇鎸囧畾鐗囨锛堟敮鎸佷粠涓棿鎴彇锛?                start_ms = int(seg.start_time_seconds * 1000)
                duration_ms = int(seg.duration_seconds * 1000)
                end_ms = min(start_ms + duration_ms, len(audio))
                
                if start_ms >= len(audio):
                    print(f"  璀﹀憡: 寮€濮嬫椂闂磋秴鍑洪煶棰戦暱搴︼紝璺宠繃")
                    continue
                
                clip = audio[start_ms:end_ms]
                print(f"  鎴彇: {seg.start_time_seconds:.1f}s - {end_ms/1000:.1f}s (闀垮害: {len(clip)/1000:.1f}s)")
                
                # 3. 閫熷害璋冩暣锛堜娇鐢╢fmpeg atempo锛屾敮鎸佸噺閫燂級
                if abs(seg.speed_ratio - 1.0) > 0.01:
                    # 纭繚閫熷害鍦ㄥ悎鐞嗚寖鍥村唴锛?.25-4.0
                    safe_speed = max(0.25, min(4.0, float(seg.speed_ratio)))
                    clip = self._ffmpeg_tempo(clip, safe_speed)
                    print(f"  閫熷害璋冩暣: {seg.speed_ratio:.2f}x (搴旂敤: {safe_speed:.2f}x)")
                
                # 4. 闊宠皟璋冩暣锛堜娇鐢╢fmpeg asetrate+atempo缁勫悎锛?                if abs(seg.pitch_shift) > 0.1:
                    # 纭繚闊宠皟璋冩暣鍦ㄥ悎鐞嗚寖鍥村唴锛?12鍒?12鍗婇煶
                    safe_pitch = max(-12.0, min(12.0, float(seg.pitch_shift)))
                    clip = self._ffmpeg_pitch_shift(clip, safe_pitch)
                    direction = "鍗囬珮" if safe_pitch > 0 else "闄嶄綆"
                    print(f"  闊宠皟璋冩暣: {seg.pitch_shift:.1f} 鍗婇煶 ({direction})")
                
                # 5. 闊抽噺璋冩暣锛坉B = 20*log10(gain)锛?                if abs(seg.volume_adjust - 1.0) > 0.01:
                    # 纭繚闊抽噺鍦ㄥ悎鐞嗚寖鍥村唴锛?.1-3.0
                    safe_volume = max(0.1, min(3.0, float(seg.volume_adjust)))
                    try:
                        gain_db = 20.0 * math.log10(safe_volume)
                    except Exception:
                        gain_db = 0.0
                    clip = clip.apply_gain(gain_db)
                    direction = "澧炲ぇ" if safe_volume > 1.0 else "鍑忓皬"
                    print(f"  闊抽噺璋冩暣: {seg.volume_adjust:.2f}x ({direction}, {gain_db:+.1f}dB)")
                
                # 6. 娣″叆娣″嚭锛堥檺鍒朵笉瓒呰繃鐗囨闀垮害鐨?0%锛?                max_fade = int(len(clip) * 0.3)
                fade_in_ms = min(max(0, int(seg.fade_in_ms)), max_fade)
                fade_out_ms = min(max(0, int(seg.fade_out_ms)), max_fade)
                if fade_in_ms or fade_out_ms:
                    clip = clip.fade_in(fade_in_ms).fade_out(fade_out_ms)
                print(f"  娣″叆娣″嚭: {fade_in_ms}ms / {fade_out_ms}ms")
                
                # 7. 鎷兼帴鍒颁富闊宠建锛堜氦鍙夋笎鍙樹笉瓒呰繃涓ょ鐗囨闀垮害锛?                if compiled is None:
                    compiled = clip
                else:
                    effective_crossfade = min(crossfade_ms, len(compiled) - 1, len(clip) - 1) if len(compiled) > 1 and len(clip) > 1 else 0
                    if effective_crossfade > 0:
                        compiled = compiled.append(clip, crossfade=effective_crossfade)
                        print(f"  浜ゅ弶娓愬彉: {effective_crossfade}ms")
                    else:
                        compiled = compiled + clip
                        print(f"  浜ゅ弶娓愬彉: 0ms (鐗囨杩囩煭锛屾敼涓虹洿鎺ユ嫾鎺?")
                
                total_ms += len(clip)
                
                # 璁板綍澶勭悊鏃ュ織
                processing_log.append({
                    "title": seg.title,
                    "start_time": seg.start_time_seconds,
                    "duration": seg.duration_seconds,
                    "speed_ratio": seg.speed_ratio,
                    "pitch_shift": seg.pitch_shift,
                    "volume_adjust": seg.volume_adjust,
                    "reason": seg.processing_reason
                })
                
            except Exception as e:
                print(f"澶勭悊闊抽鐗囨 {seg.title} 鏃跺嚭閿? {e}")
                continue

        if compiled is None:
            print("[WARN] No audio segments available")
            return None
        # 8. 瀵煎嚭鏈€缁堥煶棰?        out_path = os.path.join(self.output_dir, output_filename)
        compiled.export(out_path, format="mp3")
        
        print(f"\n闊抽鍚堟垚瀹屾垚:")
        print(f"  杈撳嚭鏂囦欢: {out_path}")
        print(f"  鎬绘椂闀? {total_ms/1000:.1f}s")
        print(f"  澶勭悊浜?{len(processing_log)} 涓煶棰戠墖娈?)
        
        return out_path, total_ms
    
    # 淇濈暀鍘熷鐨勭畝鍗曞悎鎴愭柟娉曚綔涓哄悗澶?    def synthesize_audio(self, segments: List[MusicSegment], output_filename: str = "gim_program_mix.mp3",
                          fade_in_ms: int = 1000, fade_out_ms: int = 1500, crossfade_ms: int = 2000,
                          speed_ratio: float = None) -> Tuple[str, int]:
        """绠€鍗曠殑闊抽鍚堟垚鏂规硶锛堜繚鎸佸悜鍚庡吋瀹癸級"""
        if not which("ffmpeg") and not which("ffmpeg.exe"):
            raise RuntimeError("闇€瑕佺郴缁熷凡瀹夎 ffmpeg 鎵嶈兘璇诲彇鍜屽鍑?MP3銆傝鍏堝畨瑁?ffmpeg 骞剁‘淇濆湪 PATH 涓€?)

        compiled: AudioSegment = None
        total_ms = 0

        for idx, seg in enumerate(segments):
            if not seg.filename or not os.path.exists(seg.filename):
                continue

            audio = AudioSegment.from_file(seg.filename)

            # 鍙€夐€熷害璋冩暣锛堣皑鎱庝娇鐢級
            if speed_ratio and speed_ratio > 0 and abs(speed_ratio - 1.0) > 1e-3:
                # 浣跨敤鍐呯疆鐨剆peedup锛堜細鏀瑰彉闊抽珮锛涘鏋滈渶瑕佷笉鍙樿皟锛屽彲闆嗘垚librosa鎴杝ox锛?                audio = speedup(audio, playback_speed=speed_ratio, chunk_size=50, crossfade=0)

            # 鎴彇鎵€闇€鏃堕暱
            need_ms = max(0, seg.duration_seconds * 1000)
            clip = audio[:need_ms] if len(audio) >= need_ms else audio

            # 娓愬彉
            clip = clip.fade_in(fade_in_ms).fade_out(fade_out_ms)

            # 鎷兼帴
            if compiled is None:
                compiled = clip
            else:
                compiled = compiled.append(clip, crossfade=crossfade_ms)
            total_ms += len(clip)

        if compiled is None:
            print("[WARN] No audio segments available")
            return None

        out_path = os.path.join(self.output_dir, output_filename)
        compiled.export(out_path, format="mp3")
        return out_path, total_ms

    # ------------------------
    # 楂樺眰灏佽锛氫粠瀵硅瘽鍒版櫤鑳藉悎鎴?    # ------------------------
    def build_and_render_program(self, chat_history: List[Dict[str, str]], 
                                progress_callback: Optional[callable] = None) -> Dict[str, Any]:
        """瀹屾暣鐨凣IM Program鏋勫缓鍜屾覆鏌撴祦绋?        
        Args:
            chat_history: 瀵硅瘽鍘嗗彶
            progress_callback: 杩涘害鍥炶皟鍑芥暟锛屾帴鏀跺弬鏁?
                - stage: 褰撳墠闃舵 ("analysis", "design", "music_search", "processing", "synthesis")
                - status: 鐘舵€佷俊鎭?                - progress: 杩涘害鍊?(0-100)
                - data: 鐩稿叧鏁版嵁
        """
        def update_progress(stage: str, status: str, progress: float = 0, data: Any = None):
            if progress_callback:
                progress_callback(stage, status, progress, data)
            print(f"[{stage}] {status} - {progress}%")
        
        print("=== 寮€濮婫IM Program鏋勫缓娴佺▼ ===")
        update_progress("analysis", "寮€濮嬪垎鏋愬璇濆唴瀹?..", 0)
        
        # Step 1: 瀵硅瘽鐞嗚В涓庢不鐤楁剰鍥惧垎鏋?        analysis = self.analyze_dialogue(chat_history)
        update_progress("analysis", "鍒嗘瀽瀹屾垚", 100, {
            "emotion": analysis.get('current_emotion', '鏈煡'),
            "goal": analysis.get('therapeutic_goal', '鏈瀹?),
            "themes": analysis.get('key_themes', [])
        })
        
        # Step 2: 璁捐Program妗嗘灦
        update_progress("design", "寮€濮嬭璁℃不鐤桺rogram...", 0)
        phases = self.design_program(analysis)
        update_progress("design", "璁捐瀹屾垚", 100, {
            "phase_count": len(phases),
            "phases": [{
                "name": p.phase,
                "duration": p.duration_seconds,
                "purpose": p.purpose
            } for p in phases]
        })
        
        # Step 3: 妫€绱㈤煶涔愬€欓€夐」
        update_progress("music_search", "寮€濮嬫绱㈤煶涔?..", 0)
        phase_candidates = self.retrieve_music_candidates(phases, candidates_per_phase=CANDIDATES_PER_PHASE)
        update_progress("music_search", "妫€绱㈠畬鎴?, 100, {
            "candidates": {
                phase: [{"title": c.title, "duration": c.duration_seconds} for c in candidates]
                for phase, candidates in phase_candidates.items()
            }
        })
        
        # Step 4: 鏅鸿兘閫夋嫨鍜屽鐞嗚璁★紙浣跨敤骞惰鐗堟湰锛?        update_progress("processing", "寮€濮嬪鐞嗛煶涔?..", 0)
        
        # 鍒涘缓涓€涓繘搴﹁拷韪殑鍥炶皟
        processed_count = 0
        total_phases = len([p for p in phases if phase_candidates.get(p.phase)])
        
        def processing_progress(phase_name: str, status: str, is_complete: bool = False):
            nonlocal processed_count
            if is_complete:
                processed_count += 1
            progress = (processed_count / total_phases) * 100 if total_phases > 0 else 100
            update_progress("processing", f"澶勭悊闃舵: {phase_name} - {status}", progress)
        
        processed_segments = self.design_music_processing_parallel(
            phases, 
            phase_candidates,
            progress_callback=processing_progress
        )
        
        update_progress("processing", "闊充箰澶勭悊瀹屾垚", 100, {
            "segments": [{
                "title": seg.title,
                "duration": seg.duration_seconds,
                "processing": {
                    "speed": seg.speed_ratio,
                    "pitch": seg.pitch_shift,
                    "volume": seg.volume_adjust,
                    "fade_in": seg.fade_in_ms,
                    "fade_out": seg.fade_out_ms
                }
            } for seg in processed_segments]
        })
        
        # Step 5: 楂樼骇闊抽鍚堟垚
        update_progress("synthesis", "寮€濮嬮煶棰戝悎鎴?..", 0)
        output_path, total_ms = self.synthesize_advanced_audio(processed_segments)
        
        # 鏋勫缓璇︾粏鐨勭粨鏋滄姤鍛?        program_json = {
            "analysis": analysis,
            "program": [
                {
                    "phase": p.phase,
                    "duration_seconds": p.duration_seconds,
                    "purpose": p.purpose,
                    "search_criteria": p.search_criteria,
                }
                for p in phases
            ],
            "candidates": {
                phase: [
                    {
                        "title": c.title,
                        "filename": c.filename,
                        "duration_seconds": c.duration_seconds,
                    }
                    for c in candidates
                ]
                for phase, candidates in phase_candidates.items()
            },
            "processed_segments": [
                {
                    "title": s.title,
                    "filename": s.filename,
                    "start_time_seconds": s.start_time_seconds,
                    "duration_seconds": s.duration_seconds,
                    "speed_ratio": s.speed_ratio,
                    "pitch_shift": s.pitch_shift,
                    "volume_adjust": s.volume_adjust,
                    "processing_reason": s.processing_reason,
                }
                for s in processed_segments
            ],
            "output": {
                "file": output_path,
                "total_seconds": total_ms // 1000,
            },
        }
        
        print("=== GIM Program鏋勫缓瀹屾垚 ===")
        return program_json
    
    # 淇濈暀绠€鍖栫増鏈綔涓哄悗澶?    def build_and_render_program_simple(self, chat_history: List[Dict[str, str]]) -> Dict[str, Any]:
        """绠€鍖栫殑Program鏋勫缓娴佺▼锛堝悜鍚庡吋瀹癸級"""
        analysis = self.analyze_dialogue(chat_history)
        phases = self.design_program(analysis)
        
        # 浣跨敤绠€鍖栫殑闊充箰妫€绱?        segments = []
        for phase in phases:
            criteria = phase.search_criteria or {}
            tracks = self.music_db.retrieve_music_for_therapy(criteria, num_tracks=1) or []
            if tracks:
                chosen = tracks[0]
                filename = chosen.get("filename", "")
                title = chosen.get("title", filename)
                
                if filename and not os.path.isabs(filename):
                    candidate = os.path.abspath(
                        os.path.join(os.path.dirname(__file__), "../toy_dataset/mp3", os.path.basename(filename))
                    )
                    if os.path.exists(candidate):
                        filename = candidate
                        
                segments.append(MusicSegment(filename=filename, duration_seconds=phase.duration_seconds, title=title))
        
        output_path, total_ms = self.synthesize_audio(segments)

        program_json = {
            "analysis": analysis,
            "program": [
                {
                    "phase": p.phase,
                    "duration_seconds": p.duration_seconds,
                    "purpose": p.purpose,
                    "search_criteria": p.search_criteria,
                }
                for p in phases
            ],
            "segments": [
                {
                    "title": s.title,
                    "filename": s.filename,
                    "duration_seconds": s.duration_seconds,
                }
                for s in segments
            ],
            "output": {
                "file": output_path,
                "total_seconds": total_ms // 1000,
            },
        }
        return program_json

    # ------------------------
    # 宸ュ叿锛歀LM 璋冪敤涓?JSON 鎻愬彇
    # ------------------------

    def _call_llm(self, model_name, messages: List[Dict[str, str]], timeout: int = 60) -> str:
        """璋冪敤LLM妯″瀷锛屽甫鏈夎秴鏃舵帶鍒?        
        Args:
            model_name: 妯″瀷鍚嶇О
            messages: 娑堟伅鍒楄〃
            timeout: 瓒呮椂鏃堕棿锛堢锛夛紝榛樿60绉?        """
        payload = json.dumps({
            "model": model_name,
            "max_tokens": MAX_TOKENS_RESPONSE,
            "temperature": 0.2,
            "messages": messages,
        })
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Connection': 'close'
        }
        
        # 閲嶈瘯绛栫暐锛氭渶澶?娆★紝鎸囨暟閫€閬?        last_err = None
        for attempt in range(3):
            try:
                # 浣跨敤瓒呮椂璁剧疆
                conn = http.client.HTTPSConnection("api.openai.com", timeout=timeout)
                start_time = time.time()
                
                try:
                    conn.request("POST", "/v1/chat/completions", payload, headers)
                    response = conn.getresponse()
                    raw = response.read()
                    
                    # 妫€鏌ユ槸鍚﹁秴鏃?                    if time.time() - start_time > timeout:
                        raise TimeoutError(f"LLM璋冪敤瓒呮椂锛坽timeout}绉掞級")
                    
                    try:
                        text = raw.decode("utf-8")
                    except Exception:
                        text = raw.decode("utf-8", errors="ignore")
                    
                    try:
                        response_data = json.loads(text)
                        if isinstance(response_data, dict) and 'choices' in response_data:
                            return response_data['choices'][0]['message']['content']
                        elif isinstance(response_data, dict) and 'content' in response_data:
                            return response_data['content'][0]['text']
                        return ""
                    except json.decoder.JSONDecodeError:
                        # 鍙兘鏄綉鍏宠繑鍥濰TML鎴栫┖鍝嶅簲锛岄噸璇?                        last_err = f"Invalid JSON: {text[:200]}"
                        
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
                        
            except (http.client.RemoteDisconnected, http.client.CannotSendRequest, 
                    http.client.ResponseNotReady, socket.timeout, TimeoutError) as e:
                last_err = str(e)
                print(f"LLM璋冪敤鍑洪敊锛堝皾璇?{attempt + 1}/3锛? {e}")
            except Exception as e:
                last_err = str(e)
                print(f"LLM璋冪敤鏈煡閿欒锛堝皾璇?{attempt + 1}/3锛? {e}")
            
            # 閫€閬跨瓑寰?            wait_time = min(1.5 * (attempt + 1), 5)  # 鏈€澶氱瓑寰?绉?            print(f"绛夊緟 {wait_time:.1f} 绉掑悗閲嶈瘯...")
            time.sleep(wait_time)
        
        print(f"LLM璋冪敤澶辫触: {last_err}")
        return ""

    def _extract_json(self, text: str) -> Any:
        if not text:
            return None
        # 鎻愬彇浠ｇ爜鍧椾腑鐨?JSON
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if json_match:
            text = json_match.group(1)
        # 灏濊瘯瑙ｆ瀽
        try:
            return json.loads(text)
        except Exception:
            # 灏濊瘯鎻愬彇绗竴涓?{ .. }
            brace_match = re.search(r"\{[\s\S]*\}", text)
            if brace_match:
                try:
                    return json.loads(brace_match.group(0))
                except Exception:
                    return None
            # 鎴栬€?[ .. ]
            bracket_match = re.search(r"\[[\s\S]*\]", text)
            if bracket_match:
                try:
                    return json.loads(bracket_match.group(0))
                except Exception:
                    return None
            return None 

