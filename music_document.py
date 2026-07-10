from __future__ import annotations
from elasticsearch_dsl import Document, Text, Keyword, Integer, Float, Boolean, Index
from elasticsearch_dsl.connections import connections
from elasticsearch_dsl import Q
import logging
import time

# 閰嶇疆鏃ュ織
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 瀹氫箟绱㈠紩鍜屾槧灏?class Music(Document):
    """闊充箰鏂囨。绫伙紝鐢ㄤ簬Elasticsearch绱㈠紩"""
    
    # 鍩烘湰瀛楁
    title = Text(analyzer='standard')
    filename = Keyword()
    url = Keyword()
    mp3_url = Keyword()
    
    # 鏍囩鍜屽垎绫诲瓧娈?    tags = Keyword(multi=True)
    genre = Keyword(multi=True)
    mood = Keyword(multi=True)
    movement = Keyword(multi=True)
    theme = Keyword(multi=True)
    
    # 闊抽鐗瑰緛瀛楁
    tempo = Float()
    dynamics_rmse_mean = Float()
    dynamics_rmse_std = Float()
    timbre_mfcc_mean = Float()
    pitch_chroma_mean = Float()
    rhythm_beats_frames = Integer(multi=True)
    error = Keyword(null_value="NULL")
    
    class Index:
        name = "music_therapy"
        settings = {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {
                "analyzer": {
                    "text_analyzer": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding"]
                    }
                }
            }
        }

class MusicDocumentManager:
    """闊充箰鏂囨。绠＄悊鍣紝鐢ㄤ簬绠＄悊Elasticsearch涓殑闊充箰鏁版嵁"""
    
    def __init__(self, es_host="http://localhost:9200", rebuild_index=False):
        """鍒濆鍖栭煶涔愭枃妗ｇ鐞嗗櫒"""
        self.es_host = es_host
        self.index_name = "music_therapy"
        self.music_document=None
        
        # 杩炴帴Elasticsearch
        connections.create_connection(hosts=[es_host])
        
        # 妫€鏌ユ槸鍚﹂渶瑕侀噸寤虹储寮?        if rebuild_index and Index(self.index_name).exists():
            logger.info(f"閲嶅缓绱㈠紩妯″紡锛氬垹闄ゅ凡瀛樺湪鐨勭储寮?{self.index_name}")
            Index(self.index_name).delete()
            # 鍒涘缓鏂扮储寮?            Music.init()
            logger.info(f"鎴愬姛閲嶅缓绱㈠紩: {self.index_name}")
        elif Index(self.index_name).exists():
            logger.info(f"绱㈠紩 {self.index_name} 宸插瓨鍦紝璺宠繃鍒涘缓")
        else:
            # 鍒涘缓绱㈠紩
            Music.init()
            logger.info(f"鎴愬姛鍒涘缓绱㈠紩: {self.index_name}")
    
    def create_music_document(self, music_data):
        """浠庨煶涔愭暟鎹垱寤篗usic鏂囨。瀵硅薄"""
        try:
            # 鍒涘缓Music鏂囨。瀹炰緥
            music_doc = Music()
            
            # 璁剧疆鍩烘湰瀛楁
            music_doc.title = music_data.get('title', '')
            music_doc.filename = music_data.get('filename', '')
            music_doc.url = music_data.get('url', '')
            music_doc.mp3_url = music_data.get('mp3_url', '')
            
            # 璁剧疆鏍囩鍜屽垎绫诲瓧娈?            music_doc.tags = [tag.lower() for tag in music_data.get('tags', [])]
            music_doc.genre = [genre.lower() for genre in music_data.get('genre', [])]
            music_doc.mood = [mood.lower() for mood in music_data.get('mood', [])]
            music_doc.movement = [movement.lower() for movement in music_data.get('movement', [])]
            music_doc.theme = [theme.lower() for theme in music_data.get('theme', [])]
            
            # 璁剧疆闊抽鐗瑰緛瀛楁
            audio_features = music_data.get('audio_features', {})
            music_doc.tempo = audio_features.get('tempo', 0.0)
            music_doc.dynamics_rmse_mean = audio_features.get('dynamics_rmse_mean', 0.0)
            music_doc.dynamics_rmse_std = audio_features.get('dynamics_rmse_std', 0.0)
            music_doc.timbre_mfcc_mean = audio_features.get('timbre_mfcc_mean', 0.0)
            music_doc.pitch_chroma_mean = audio_features.get('pitch_chroma_mean', 0.0)
            music_doc.rhythm_beats_frames = audio_features.get('rhythm_beats_frames', [])
            music_doc.error = audio_features.get('error', 'NULL')
            
            self.music_document=music_doc
            return music_doc
            
        except Exception as e:
            logger.error(f"鍒涘缓闊充箰鏂囨。鏃跺嚭閿? {str(e)}")
            self.music_document=None
            return None
    
    def save_music_document(self, music_doc):
        """淇濆瓨闊充箰鏂囨。鍒癊lasticsearch"""
        try:
            music_doc.save()
            logger.info(f"鎴愬姛淇濆瓨闊充箰鏂囨。: {music_doc.title}")
            return True
        except Exception as e:
            logger.error(f"淇濆瓨闊充箰鏂囨。鏃跺嚭閿? {str(e)}")
            return False
    
    def search_music(self, criteria, size=4):
        """鍩轰簬缁欏畾鏉′欢鎼滅储闊充箰"""
        # try:
        # 鏋勫缓鎼滅储鏌ヨ
        print("criteria", criteria)
        query = self._build_search_query(criteria)

        print("query", query)
        
        ###record search time
        start_time = time.time()
        # 鎵ц鎼滅储
        search = Music.search()        
        search = search.query(query)
        search = search.extra(size=size)
        
        response = search.execute()
        end_time = time.time()
        search_time = end_time - start_time
        print(f"Search time: {search_time} seconds")
        
        print("response", response)

        # 鎻愬彇缁撴灉
        start_time = time.time()
        tracks = []
        for hit in response:
            track = hit.to_dict()
            print("track", track)
            track['match_score'] = hit.meta.score
            tracks.append(track)
        
        end_time = time.time()
        search_time = end_time - start_time
        print(f"!!!Search time: {search_time} seconds")
        
        logger.info(f"鎼滅储瀹屾垚锛屾壘鍒?{len(tracks)} 涓粨鏋?)
        return tracks
            
        # except Exception as e:
        #     logger.error(f"鎼滅储闊充箰鏃跺嚭閿? {str(e)}")
        #     return []
    
    def _build_search_query(self, criteria):
        """鏋勫缓鎼滅储鏌ヨ"""
        
        must_queries = []
        should_queries = []
        must_not_queries = []
        
        # 澶勭悊tempo鍋忓ソ
        if criteria.get("tempo_preference"):
            tempo_range = {
                "slow": {"lt": 80},
                "medium": {"gte": 80, "lte": 120},
                "fast": {"gt": 120}
            }.get(criteria["tempo_preference"])
            
            if tempo_range:
                must_queries.append(Q("range", tempo=tempo_range))
        
        # 澶勭悊dynamics鍋忓ソ
        if criteria.get("dynamics_preference"):
            dynamics_range = {
                "soft": {"lt": 0.1},
                "moderate": {"gte": 0.1, "lte": 0.2},
                "intense": {"gt": 0.2}
            }.get(criteria["dynamics_preference"])
            
            if dynamics_range:
                must_queries.append(Q("range", dynamics_rmse_mean=dynamics_range))
        
        # 澶勭悊mood鍏抽敭璇?        if criteria.get("mood_keywords"):
            mood_terms = [m.lower() for m in criteria["mood_keywords"]]
            should_queries.append(Q("terms", mood=mood_terms))
            should_queries.append(Q("match", title=" ".join(criteria["mood_keywords"])))
        
        # 澶勭悊genre鍏抽敭璇?        if criteria.get("genre_keywords"):
            genre_terms = [g.lower() for g in criteria["genre_keywords"]]
            should_queries.append(Q("terms", genre=genre_terms))
            should_queries.append(Q("match", title=" ".join(criteria["genre_keywords"])))
        
        # 澶勭悊闇€瑕侀伩鍏嶇殑鍏抽敭璇?        if criteria.get("avoid_keywords"):
            avoid_terms = [a.lower() for a in criteria["avoid_keywords"]]
            must_not_queries.extend([
                Q("terms", tags=avoid_terms),
                Q("terms", mood=avoid_terms),
                Q("terms", genre=avoid_terms),
                Q("terms", theme=avoid_terms)
            ])
        
        # 涓€姝ユ瀯閫犲竷灏旀煡璇紝閬垮厤閾惧紡璧嬪€?        bool_params = {}
        if must_queries:
            bool_params['must'] = must_queries
        if should_queries:
            bool_params['should'] = should_queries
            bool_params['minimum_should_match'] = 1
        if must_not_queries:
            bool_params['must_not'] = must_not_queries
        
        bool_query = Q("bool", **bool_params)
        return bool_query
    
    def get_all_music(self):
        """鑾峰彇鎵€鏈夐煶涔愭暟鎹?""
        try:
            search = Music.search()
            search = search.extra(size=10000)  # 鑾峰彇鎵€鏈夋暟鎹?            response = search.execute()
            
            tracks = []
            for hit in response:
                track = hit.to_dict()
                tracks.append(track)
            
            return tracks
            
        except Exception as e:
            logger.error(f"鑾峰彇鎵€鏈夐煶涔愭暟鎹椂鍑洪敊: {str(e)}")
            return []
    
    def get_attribute_options(self, attribute_type, max_items=30):
        """鑾峰彇鎸囧畾灞炴€х殑鎵€鏈夐€夐」"""
        try:
            search = Music.search()
            search = search.extra(size=0)  # 涓嶉渶瑕佹枃妗ｏ紝鍙渶瑕佽仛鍚堢粨鏋?            
            # 娣诲姞鑱氬悎鏌ヨ
            search.aggs.bucket('unique_values', 'terms', field=attribute_type, size=max_items)
            
            response = search.execute()
            
            # 鎻愬彇鑱氬悎缁撴灉
            buckets = response.aggregations.unique_values.buckets
            options = [bucket.key for bucket in buckets]
            
            return options
            
        except Exception as e:
            logger.error(f"鑾峰彇灞炴€ч€夐」鏃跺嚭閿? {str(e)}")
            return []

def create_music_index():
    """鍒涘缓闊充箰绱㈠紩鐨勪究鎹峰嚱鏁?""
    try:
        manager = MusicDocumentManager()
        logger.info("闊充箰绱㈠紩鍒涘缓鎴愬姛")
        return manager
    except Exception as e:
        logger.error(f"鍒涘缓闊充箰绱㈠紩澶辫触: {str(e)}")
        return None

if __name__ == "__main__":
    # 娴嬭瘯鍒涘缓绱㈠紩
    manager = create_music_index()
    if manager:
        print("闊充箰绱㈠紩鍒涘缓鎴愬姛锛?)
    else:
        print("闊充箰绱㈠紩鍒涘缓澶辫触锛?) 
