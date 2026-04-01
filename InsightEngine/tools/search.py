"""
专为 AI Agent 设计的本地舆情数据库查询工具集 (MediaCrawlerDB)

版本: 3.0
最后更新: 2025-08-23

此脚本将复杂的本地MySQL数据库查询功能封装成一系列目标明确、参数清晰的独立工具，
专为AI Agent调用而设计。Agent只需根据任务意图（如搜索热点、全局搜索话题、
按时间范围分析、获取评论）选择合适的工具，无需编写复杂的SQL语句。

V3.0 核心更新:
- 智能热度计算: `search_hot_content`不再需要`sort_by`参数，改为内部使用统一的加权热度算法，
  综合点赞、评论、分享、观看等数据计算热度分值，使结果更智能、更符合综合热度。
- 新增平台精搜工具: 新增 `search_topic_on_platform` 工具，作为特例，
  允许Agent在特定平台（B站、微博等七大平台）上对某一话题进行精确搜索，并支持时间筛选。
- 结构优化: 调整了数据结构与函数文档，以适应新功能。

主要工具:
- search_hot_content: 查找指定时间范围内的综合热度最高的内容。
- search_topic_globally: 在整个数据库中全局搜索与特定话题相关的所有内容和评论。
- search_topic_by_date: 在指定的历史日期范围内搜索与特定话题相关的内容。
- get_comments_for_topic: 专门提取公众对于某一特定话题的评论数据。
- search_topic_on_platform: 在指定的单个社交媒体平台上搜索特定话题。
"""

import os
import json
from loguru import logger
import asyncio
from typing import List, Dict, Any, Optional, Literal
from dataclasses import dataclass, field
from ..utils.db import fetch_all
from datetime import datetime, timedelta, date
from InsightEngine.utils.config import settings

# --- 1. 数据结构定义 ---

@dataclass
class QueryResult:
    """统一的数据库查询结果数据类"""
    platform: str
    content_type: str
    title_or_content: str
    author_nickname: Optional[str] = None
    url: Optional[str] = None
    publish_time: Optional[datetime] = None
    engagement: Dict[str, int] = field(default_factory=dict)
    source_keyword: Optional[str] = None
    hotness_score: float = 0.0
    source_table: str = ""

@dataclass
class DBResponse:
    """封装工具的完整返回结果"""
    tool_name: str
    parameters: Dict[str, Any]
    results: List[QueryResult] = field(default_factory=list)
    results_count: int = 0
    error_message: Optional[str] = None

# --- 2. 核心客户端与专用工具集 ---

class MediaCrawlerDB:
    """包含多种专用舆情数据库查询工具的客户端"""
    # 权重定义
    W_LIKE = 1.0
    W_COMMENT = 5.0
    W_SHARE = 10.0  # 分享/转发/收藏/投币等高价值互动
    W_VIEW = 0.1
    W_DANMAKU = 0.5

    def __init__(self):
        """
        初始化客户端。
        """
        pass
        
    def _execute_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        try:
            # 获取或创建event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # 直接运行协程
            return loop.run_until_complete(fetch_all(query, params))
        
        except Exception as e:
            logger.exception(f"数据库查询时发生错误: {e}")
            return []

    def _is_postgresql(self) -> bool:
        return (settings.DB_DIALECT or "mysql").lower() in {"postgresql", "postgres"}

    def _like_operator(self) -> str:
        return "ILIKE" if self._is_postgresql() else "LIKE"

    def _numeric_sql(self, expression: str) -> str:
        """返回兼容当前数据库方言的数值转换表达式。"""
        if self._is_postgresql():
            normalized = f"BTRIM(COALESCE(({expression})::text, ''))"
            return (
                "CASE "
                f"WHEN {normalized} = '' THEN 0 "
                f"WHEN {normalized} ~ '^-?[0-9]+([.][0-9]+)?$' "
                f"THEN CAST(({expression}) AS DOUBLE PRECISION) "
                "ELSE 0 END"
            )
        return f"COALESCE(CAST({expression} AS DECIMAL(20, 2)), 0)"

    def _text_sql(self, expression: str) -> str:
        if self._is_postgresql():
            return f"CAST(({expression}) AS TEXT)"
        return f"CAST({expression} AS CHAR)"

    def _build_search_clause(self, fields: List[str], param_dict: Dict[str, Any], search_term: str) -> str:
        clauses = []
        operator = self._like_operator()
        for idx, field in enumerate(fields):
            pname = f"term_{idx}"
            clauses.append(f"{self._wrap_query_field_with_dialect(field)} {operator} :{pname}")
            param_dict[pname] = search_term
        return " OR ".join(clauses)

    def _build_time_clause(
        self,
        wrapped_time_col: str,
        time_type: str,
        start_dt: datetime,
        end_dt: datetime,
        param_dict: Dict[str, Any],
    ) -> str:
        if time_type == 'ms':
            param_dict['start_time'] = int(start_dt.timestamp() * 1000)
            param_dict['end_time'] = int(end_dt.timestamp() * 1000)
            numeric_time = self._numeric_sql(wrapped_time_col)
            return f"{numeric_time} >= :start_time AND {numeric_time} < :end_time"

        if time_type in {'sec', 'sec_str'}:
            param_dict['start_time'] = int(start_dt.timestamp())
            param_dict['end_time'] = int(end_dt.timestamp())
            numeric_time = self._numeric_sql(wrapped_time_col)
            return f"{numeric_time} >= :start_time AND {numeric_time} < :end_time"

        if time_type == 'date_str':
            if self._is_postgresql():
                param_dict['start_time'] = start_dt.date()
                param_dict['end_time'] = end_dt.date()
            else:
                param_dict['start_time'] = start_dt.strftime('%Y-%m-%d')
                param_dict['end_time'] = end_dt.strftime('%Y-%m-%d')
            return f"{wrapped_time_col} >= :start_time AND {wrapped_time_col} < :end_time"

        param_dict['start_time'] = start_dt.strftime('%Y-%m-%d %H:%M:%S')
        param_dict['end_time'] = end_dt.strftime('%Y-%m-%d %H:%M:%S')
        return f"{wrapped_time_col} >= :start_time AND {wrapped_time_col} < :end_time"

    @staticmethod
    def _to_datetime(ts: Any) -> Optional[datetime]:
        if not ts: return None
        try:
            if isinstance(ts, datetime): return ts
            if isinstance(ts, date): return datetime.combine(ts, datetime.min.time())
            if isinstance(ts, (int, float)) or str(ts).isdigit():
                val = float(ts)
                return datetime.fromtimestamp(val / 1000 if val > 1_000_000_000_000 else val)
            if isinstance(ts, str):
                return datetime.fromisoformat(ts.split('+')[0].strip())
        except (ValueError, TypeError): return None

    _table_columns_cache = {}
    def _get_table_columns(self, table_name: str) -> List[str]:
        if table_name in self._table_columns_cache:
            return self._table_columns_cache[table_name]

        if self._is_postgresql():
            results = self._execute_query(
                """
                SELECT column_name AS field
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table_name
                ORDER BY ordinal_position
                """,
                {'table_name': table_name},
            )
            columns = [row['field'] for row in results] if results else []
        else:
            results = self._execute_query(f"SHOW COLUMNS FROM `{table_name}`")
            columns = [row['Field'] for row in results] if results else []

        self._table_columns_cache[table_name] = columns
        return columns

    def _extract_engagement(self, row: Dict[str, Any]) -> Dict[str, int]:
        """从数据行中提取并统一互动指标"""
        engagement = {}
        mapping = { 'likes': ['liked_count', 'like_count', 'voteup_count', 'comment_like_count'], 'comments': ['video_comment', 'comments_count', 'comment_count', 'total_replay_num', 'sub_comment_count'], 'shares': ['video_share_count', 'shared_count', 'share_count', 'total_forwards'], 'views': ['video_play_count', 'viewd_count'], 'favorites': ['video_favorite_count', 'collected_count'], 'coins': ['video_coin_count'], 'danmaku': ['video_danmaku'], }
        for key, potential_cols in mapping.items():
            for col in potential_cols:
                if col in row and row[col] is not None:
                    try: engagement[key] = int(row[col])
                    except (ValueError, TypeError): engagement[key] = 0
                    break
        return engagement

    def search_hot_content(
        self,
        time_period: Literal['24h', 'week', 'year'] = 'week',
        limit: int = 50
    ) -> DBResponse:
        """
        【工具】查找热点内容: 获取最近一段时间内综合热度最高的内容。

        Args:
            time_period (Literal['24h', 'week', 'year']): 时间范围，默认为 'week'。
            limit (int): 返回结果的最大数量，默认为 50。

        Returns:
            DBResponse: 包含按综合热度排序后的内容列表。
        """
        params_for_log = {'time_period': time_period, 'limit': limit}
        logger.info(f"--- TOOL: 查找热点内容 (params: {params_for_log}) ---")
        
        now = datetime.now()
        start_time = now - timedelta(days={'24h': 1, 'week': 7}.get(time_period, 365))
        quote = self._wrap_query_field_with_dialect
        num = lambda field_name: self._numeric_sql(quote(field_name))

        # 定义各平台的热度计算SQL片段
        hotness_formulas = {
            'bilibili_video': (
                f"({num('liked_count')} * {self.W_LIKE} + "
                f"{num('video_comment')} * {self.W_COMMENT} + "
                f"{num('video_share_count')} * {self.W_SHARE} + "
                f"{num('video_favorite_count')} * {self.W_SHARE} + "
                f"{num('video_coin_count')} * {self.W_SHARE} + "
                f"{num('video_danmaku')} * {self.W_DANMAKU} + "
                f"{num('video_play_count')} * {self.W_VIEW})"
            ),
            'douyin_aweme': (
                f"({num('liked_count')} * {self.W_LIKE} + "
                f"{num('comment_count')} * {self.W_COMMENT} + "
                f"{num('share_count')} * {self.W_SHARE} + "
                f"{num('collected_count')} * {self.W_SHARE})"
            ),
            'weibo_note': (
                f"({num('liked_count')} * {self.W_LIKE} + "
                f"{num('comments_count')} * {self.W_COMMENT} + "
                f"{num('shared_count')} * {self.W_SHARE})"
            ),
            'xhs_note': (
                f"({num('liked_count')} * {self.W_LIKE} + "
                f"{num('comment_count')} * {self.W_COMMENT} + "
                f"{num('share_count')} * {self.W_SHARE} + "
                f"{num('collected_count')} * {self.W_SHARE})"
            ),
            'kuaishou_video': (
                f"({num('liked_count')} * {self.W_LIKE} + "
                f"{num('viewd_count')} * {self.W_VIEW})"
            ),
            'zhihu_content': (
                f"({num('voteup_count')} * {self.W_LIKE} + "
                f"{num('comment_count')} * {self.W_COMMENT})"
            ),
        }

        all_queries, param_dict = [], {}
        for idx, (table, formula) in enumerate(hotness_formulas.items()):
            time_param_name = f"start_time_{idx}"
            time_col = 'create_time'
            if table == 'weibo_note':
                wrapped_time_col = quote('create_date_time')
                param_dict[time_param_name] = start_time.strftime('%Y-%m-%d %H:%M:%S')
                time_filter_sql = f"{wrapped_time_col} >= :{time_param_name}"
            elif table in ['kuaishou_video', 'xhs_note', 'douyin_aweme']:
                time_col = 'time' if table == 'xhs_note' else 'create_time'
                wrapped_time_col = quote(time_col)
                param_dict[time_param_name] = int(start_time.timestamp() * 1000)
                time_filter_sql = f"{self._numeric_sql(wrapped_time_col)} >= :{time_param_name}"
            elif table == 'zhihu_content':
                time_col = 'created_time'
                wrapped_time_col = quote(time_col)
                param_dict[time_param_name] = int(start_time.timestamp())
                time_filter_sql = f"{self._numeric_sql(wrapped_time_col)} >= :{time_param_name}"
            else:
                wrapped_time_col = quote(time_col)
                param_dict[time_param_name] = int(start_time.timestamp())
                time_filter_sql = f"{self._numeric_sql(wrapped_time_col)} >= :{time_param_name}"

            content_type = 'note' if table in ['weibo_note', 'xhs_note'] else 'content' if table == 'zhihu_content' else 'video'
            query_template = (
                "SELECT '{platform}' as p, '{type}' as t, {title} as title, {author} as author, "
                "{url} as url, {ts} as ts, {formula} as hotness_score, {source_keyword} as source_keyword, "
                "'{tbl}' as tbl FROM {table} WHERE {time_filter}"
            )
            
            field_subs = {
                'platform': table.split('_')[0],
                'type': content_type,
                'title': quote('title'),
                'author': quote('nickname'),
                'url': quote('video_url'),
                'ts': self._text_sql(quote(time_col)),
                'formula': formula,
                'source_keyword': quote('source_keyword'),
                'tbl': table,
                'table': quote(table),
                'time_filter': time_filter_sql,
            }
            if table == 'weibo_note':
                field_subs.update({'title': quote('content'), 'url': quote('note_url'), 'ts': self._text_sql(quote('create_date_time'))})
            elif table == 'xhs_note':
                field_subs.update({'ts': self._text_sql(quote('time')), 'url': quote('note_url')})
            elif table == 'zhihu_content':
                field_subs.update({'author': quote('user_nickname'), 'url': quote('content_url'), 'ts': self._text_sql(quote('created_time'))})
            elif table == 'douyin_aweme':
                field_subs.update({'url': quote('aweme_url')})

            all_queries.append(query_template.format(**field_subs))
        param_dict['limit'] = limit

        final_query = f"({' ) UNION ALL ( '.join(all_queries)}) ORDER BY hotness_score DESC LIMIT :limit"
        raw_results = self._execute_query(final_query, param_dict)

        formatted_results = [QueryResult(platform=r['p'], content_type=r['t'], title_or_content=r['title'], author_nickname=r.get('author'), url=r['url'], publish_time=self._to_datetime(r['ts']), engagement=self._extract_engagement(r), hotness_score=r.get('hotness_score', 0.0), source_keyword=r.get('source_keyword'), source_table=r['tbl']) for r in raw_results]
        return DBResponse("search_hot_content", params_for_log, results=formatted_results, results_count=len(formatted_results))    

    def _wrap_query_field_with_dialect(self, field: str) -> str:
        """根据数据库方言包装SQL查询"""
        if self._is_postgresql():
            return f'"{field}"'
        return f'`{field}`'

    def search_topic_globally(self, topic: str, limit_per_table: int = 100) -> DBResponse:
        """
        【工具】全局话题搜索: 在数据库中（内容、评论、标签、来源关键字）全面搜索指定话题。

        Args:
            topic (str): 要搜索的话题关键词。
            limit_per_table (int): 从每个相关表中返回的最大记录数，默认为 100。

        Returns:
            DBResponse: 包含所有匹配结果的聚合列表。
        """
        params_for_log = {'topic': topic, 'limit_per_table': limit_per_table}
        logger.info(f"--- TOOL: 全局话题搜索 (params: {params_for_log}) ---")
        
        search_term, all_results = f"%{topic}%", []
        search_configs = { 'bilibili_video': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video'}, 'bilibili_video_comment': {'fields': ['content'], 'type': 'comment'}, 'douyin_aweme': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video'}, 'douyin_aweme_comment': {'fields': ['content'], 'type': 'comment'}, 'kuaishou_video': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video'}, 'kuaishou_video_comment': {'fields': ['content'], 'type': 'comment'}, 'weibo_note': {'fields': ['content', 'source_keyword'], 'type': 'note'}, 'weibo_note_comment': {'fields': ['content'], 'type': 'comment'}, 'xhs_note': {'fields': ['title', 'desc', 'tag_list', 'source_keyword'], 'type': 'note'}, 'xhs_note_comment': {'fields': ['content'], 'type': 'comment'}, 'zhihu_content': {'fields': ['title', 'desc', 'content_text', 'source_keyword'], 'type': 'content'}, 'zhihu_comment': {'fields': ['content'], 'type': 'comment'}, 'tieba_note': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'note'}, 'tieba_comment': {'fields': ['content'], 'type': 'comment'}, 'daily_news': {'fields': ['title'], 'type': 'news'}, }
        
        for table, config in search_configs.items():
            param_dict = {}
            where_clause = self._build_search_clause(config['fields'], param_dict, search_term)
            param_dict['limit'] = limit_per_table
            query = f'SELECT * FROM {self._wrap_query_field_with_dialect(table)} WHERE {where_clause} ORDER BY id DESC LIMIT :limit'
            raw_results = self._execute_query(query, param_dict)
            for row in raw_results:
                content = (row.get('title') or row.get('content') or row.get('desc') or row.get('content_text', ''))
                time_key = row.get('create_time') or row.get('time') or row.get('created_time') or row.get('publish_time') or row.get('crawl_date')
                all_results.append(QueryResult(
                    platform=table.split('_')[0], content_type=config['type'],
                    title_or_content=content if content else '',
                    author_nickname=row.get('nickname') or row.get('user_nickname') or row.get('user_name'),
                    url=row.get('video_url') or row.get('note_url') or row.get('content_url') or row.get('url') or row.get('aweme_url'),
                    publish_time=self._to_datetime(time_key),
                    engagement=self._extract_engagement(row),
                    source_keyword=row.get('source_keyword'),
                    source_table=table
                ))
        return DBResponse("search_topic_globally", params_for_log, results=all_results, results_count=len(all_results))

    def search_topic_by_date(self, topic: str, start_date: str, end_date: str, limit_per_table: int = 100) -> DBResponse:
        """
        【工具】按日期搜索话题: 在明确的历史时间段内，搜索与特定话题相关的内容。

        Args:
            topic (str): 要搜索的话题关键词。
            start_date (str): 开始日期，格式 'YYYY-MM-DD'。
            end_date (str): 结束日期，格式 'YYYY-MM-DD'。
            limit_per_table (int): 从每个相关表中返回的最大记录数，默认为 100。

        Returns:
            DBResponse: 包含在指定日期范围内找到的结果的聚合列表。
        """
        params_for_log = {'topic': topic, 'start_date': start_date, 'end_date': end_date, 'limit_per_table': limit_per_table}
        logger.info(f"--- TOOL: 按日期搜索话题 (params: {params_for_log}) ---")
        
        try:
            start_dt, end_dt = datetime.strptime(start_date, '%Y-%m-%d'), datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            return DBResponse("search_topic_by_date", params_for_log, error_message="日期格式错误，请使用 'YYYY-MM-DD' 格式。")
        
        search_term, all_results = f"%{topic}%", []
        search_configs = {
            'bilibili_video': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'sec'}, 'douyin_aweme': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'ms'},
            'kuaishou_video': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'ms'}, 'weibo_note': {'fields': ['content', 'source_keyword'], 'type': 'note', 'time_col': 'create_date_time', 'time_type': 'str'},
            'xhs_note': {'fields': ['title', 'desc', 'tag_list', 'source_keyword'], 'type': 'note', 'time_col': 'time', 'time_type': 'ms'}, 'zhihu_content': {'fields': ['title', 'desc', 'content_text', 'source_keyword'], 'type': 'content', 'time_col': 'created_time', 'time_type': 'sec_str'},
            'tieba_note': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'note', 'time_col': 'publish_time', 'time_type': 'str'}, 'daily_news': {'fields': ['title'], 'type': 'news', 'time_col': 'crawl_date', 'time_type': 'date_str'},
        }

        for table, config in search_configs.items():
            param_dict = {}
            topic_clause = self._build_search_clause(config['fields'], param_dict, search_term)
            time_col = self._wrap_query_field_with_dialect(config['time_col'])
            time_clause = self._build_time_clause(time_col, config['time_type'], start_dt, end_dt, param_dict)
            param_dict['limit'] = limit_per_table
            query = (
                f'SELECT * FROM {self._wrap_query_field_with_dialect(table)} '
                f'WHERE ({topic_clause}) AND ({time_clause}) ORDER BY id DESC LIMIT :limit'
            )
            raw_results = self._execute_query(query, param_dict)
            for row in raw_results:
                content = (row.get('title') or row.get('content') or row.get('desc') or row.get('content_text', ''))
                time_key = row.get('create_time') or row.get('time') or row.get('created_time') or row.get('publish_time') or row.get('crawl_date')
                all_results.append(QueryResult(
                    platform=table.split('_')[0], content_type=config['type'],
                    title_or_content=content if content else '',
                    author_nickname=row.get('nickname') or row.get('user_nickname') or row.get('user_name'),
                    url=row.get('video_url') or row.get('note_url') or row.get('content_url') or row.get('url') or row.get('aweme_url'),
                    publish_time=self._to_datetime(time_key),
                    engagement=self._extract_engagement(row),
                    source_keyword=row.get('source_keyword'),
                    source_table=table
                ))
        all_results.sort(key=lambda item: item.publish_time or datetime.min, reverse=True)
        return DBResponse("search_topic_by_date", params_for_log, results=all_results, results_count=len(all_results))
        
    def get_comments_for_topic(self, topic: str, limit: int = 500) -> DBResponse:
        """
        【工具】获取话题评论: 专门搜索并返回所有平台中与特定话题相关的公众评论数据。

        Args:
            topic (str): 要搜索的话题关键词。
            limit (int): 返回评论的总数量上限，默认为 500。

        Returns:
            DBResponse: 包含匹配的评论列表。
        """
        params_for_log = {'topic': topic, 'limit': limit}
        logger.info(f"--- TOOL: 获取话题评论 (params: {params_for_log}) ---")
        
        search_term = f"%{topic}%"
        comment_tables = ['bilibili_video_comment', 'douyin_aweme_comment', 'kuaishou_video_comment', 'weibo_note_comment', 'xhs_note_comment', 'zhihu_comment', 'tieba_comment']
        
        all_queries = []
        param_dict: Dict[str, Any] = {}
        for table in comment_tables:
            cols = self._get_table_columns(table)
            author_col = 'user_nickname' if 'user_nickname' in cols else 'nickname' if 'nickname' in cols else 'user_name'
            like_col = 'comment_like_count' if 'comment_like_count' in cols else 'like_count' if 'like_count' in cols else None
            time_col = 'publish_time' if 'publish_time' in cols else 'create_date_time' if 'create_date_time' in cols else 'create_time'
            like_select = self._text_sql(self._wrap_query_field_with_dialect(like_col)) if like_col else "'0'"
            content_col = self._wrap_query_field_with_dialect('content')
            search_param_name = f"search_term_{len(all_queries)}"
            param_dict[search_param_name] = search_term
            
            query = (
                f"SELECT '{table.split('_')[0]}' as platform, {content_col} as content, "
                f"{self._wrap_query_field_with_dialect(author_col)} as author, "
                f"{self._text_sql(self._wrap_query_field_with_dialect(time_col))} as ts, {like_select} as likes, '{table}' as source_table "
                f"FROM {self._wrap_query_field_with_dialect(table)} "
                f"WHERE {content_col} {self._like_operator()} :{search_param_name}"
            )
            all_queries.append(query)

        final_query = f"({' ) UNION ALL ( '.join(all_queries)}) ORDER BY ts DESC LIMIT :limit"
        param_dict['limit'] = limit
        raw_results = self._execute_query(final_query, param_dict)
        
        formatted = [QueryResult(platform=r['platform'], content_type='comment', title_or_content=r['content'], author_nickname=r['author'], publish_time=self._to_datetime(r['ts']), engagement={'likes': int(r['likes']) if str(r['likes']).isdigit() else 0}, source_table=r['source_table']) for r in raw_results]
        formatted.sort(key=lambda item: item.publish_time or datetime.min, reverse=True)
        return DBResponse("get_comments_for_topic", params_for_log, results=formatted, results_count=len(formatted))

    def search_topic_on_platform(
        self,
        platform: Literal['bilibili', 'weibo', 'douyin', 'kuaishou', 'xhs', 'zhihu', 'tieba'],
        topic: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20
    ) -> DBResponse:
        """
        【工具】平台定向搜索: (新增) 在指定的单个社交媒体平台上搜索特定话题。

        Args:
            platform (Literal['bilibili', ...]): 要搜索的平台，必须是七个支持的平台之一。
            topic (str): 要搜索的话题关键词。
            start_date (Optional[str]): 开始日期，格式 'YYYY-MM-DD'。默认为None。
            end_date (Optional[str]): 结束日期，格式 'YYYY-MM-DD'。默认为None。
            limit (int): 返回结果的最大数量，默认为 20。

        Returns:
            DBResponse: 包含在该平台找到的结果列表。
        """
        params_for_log = {'platform': platform, 'topic': topic, 'start_date': start_date, 'end_date': end_date, 'limit': limit}
        logger.info(f"--- TOOL: 平台定向搜索 (params: {params_for_log}) ---")

        all_configs = { 'bilibili': [{'table': 'bilibili_video', 'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'sec'}, {'table': 'bilibili_video_comment', 'fields': ['content'], 'type': 'comment'}], 'douyin': [{'table': 'douyin_aweme', 'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'ms'}, {'table': 'douyin_aweme_comment', 'fields': ['content'], 'type': 'comment'}], 'kuaishou': [{'table': 'kuaishou_video', 'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'ms'}, {'table': 'kuaishou_video_comment', 'fields': ['content'], 'type': 'comment'}], 'weibo': [{'table': 'weibo_note', 'fields': ['content', 'source_keyword'], 'type': 'note', 'time_col': 'create_date_time', 'time_type': 'str'}, {'table': 'weibo_note_comment', 'fields': ['content'], 'type': 'comment'}], 'xhs': [{'table': 'xhs_note', 'fields': ['title', 'desc', 'tag_list', 'source_keyword'], 'type': 'note', 'time_col': 'time', 'time_type': 'ms'}, {'table': 'xhs_note_comment', 'fields': ['content'], 'type': 'comment'}], 'zhihu': [{'table': 'zhihu_content', 'fields': ['title', 'desc', 'content_text', 'source_keyword'], 'type': 'content', 'time_col': 'created_time', 'time_type': 'sec_str'}, {'table': 'zhihu_comment', 'fields': ['content'], 'type': 'comment'}], 'tieba': [{'table': 'tieba_note', 'fields': ['title', 'desc', 'source_keyword'], 'type': 'note', 'time_col': 'publish_time', 'time_type': 'str'}, {'table': 'tieba_comment', 'fields': ['content'], 'type': 'comment'}] }
        
        if platform not in all_configs:
            return DBResponse("search_topic_on_platform", params_for_log, error_message=f"不支持的平台: {platform}")

        search_term, all_results = f"%{topic}%", []
        platform_configs = all_configs[platform]
        if start_date and end_date:
            try:
                start_dt, end_dt = datetime.strptime(start_date, '%Y-%m-%d'), datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            except ValueError:
                return DBResponse("search_topic_on_platform", params_for_log, error_message="日期格式错误，请使用 'YYYY-MM-DD' 格式。")
        else:
            start_dt, end_dt = None, None

        for config in platform_configs:
            table = config['table']
            param_dict = {}
            topic_clause = self._build_search_clause(config['fields'], param_dict, search_term)
            query = f"SELECT * FROM {self._wrap_query_field_with_dialect(table)} WHERE ({topic_clause})"

            if start_dt and end_dt and 'time_col' in config:
                time_col = config['time_col']
                wrapped_time_col = self._wrap_query_field_with_dialect(time_col)
                t_clause = self._build_time_clause(wrapped_time_col, config['time_type'], start_dt, end_dt, param_dict)
                query += f" AND ({t_clause})"

            query += " ORDER BY id DESC LIMIT :limit"
            param_dict['limit'] = limit

            raw_results = self._execute_query(query, param_dict)
            for row in raw_results:
                content = (row.get('title') or row.get('content') or row.get('desc') or row.get('content_text', ''))
                time_key = config.get('time_col') and row.get(config.get('time_col'))
                all_results.append(QueryResult(platform=platform, content_type=config['type'], title_or_content=content if content else '', author_nickname=row.get('nickname') or row.get('user_nickname'), url=row.get('video_url') or row.get('note_url') or row.get('content_url') or row.get('url') or row.get('aweme_url'), publish_time=self._to_datetime(time_key), engagement=self._extract_engagement(row), source_keyword=row.get('source_keyword'), source_table=table))
        all_results.sort(key=lambda item: item.publish_time or datetime.min, reverse=True)
        return DBResponse("search_topic_on_platform", params_for_log, results=all_results, results_count=len(all_results))

# --- 3. 测试与使用示例 ---
def print_response_summary(response: DBResponse):
    """简化的打印函数，用于展示测试结果"""
    if response.error_message:
        logger.info(f"工具 '{response.tool_name}' 执行出错: {response.error_message}")
        return

    params_str = ", ".join(f"{k}='{v}'" for k, v in response.parameters.items())
    logger.info(f"查询: 工具='{response.tool_name}', 参数=[{params_str}]")
    logger.info(f"找到 {response.results_count} 条相关记录。")
    
    # 统一为一个消息输出
    output_lines = []
    output_lines.append("==== 查询结果预览（最多前5条） ====")
    if response.results and len(response.results) > 0:
        for idx, res in enumerate(response.results[:5], 1):
            content_preview = (res.title_or_content.replace('\n', ' ')[:70] + '...') if res.title_or_content and len(res.title_or_content) > 70 else (res.title_or_content or '')
            author_str = res.author_nickname or "N/A"
            publish_time_str = res.publish_time.strftime('%Y-%m-%d %H:%M') if res.publish_time else "N/A"
            hotness_str = f", hotness: {res.hotness_score:.2f}" if getattr(res, "hotness_score", 0) > 0 else ""
            engagement_dict = getattr(res, "engagement", {}) or {}
            engagement_str = ", ".join(f"{k}: {v}" for k, v in engagement_dict.items() if v)
            output_lines.append(
                f"{idx}. [{res.platform.upper()}/{res.content_type}] {content_preview}\n"
                f"   作者: {author_str} | 时间: {publish_time_str}"
                f"{hotness_str} | 源关键词: '{res.source_keyword or 'N/A'}'\n"
                f"   链接: {res.url or 'N/A'}\n"
                f"   互动数据: {{{engagement_str}}}"
            )
    else:
        output_lines.append("暂无相关内容。")
    output_lines.append("=" * 60)
    logger.info('\n'.join(output_lines))

if __name__ == "__main__":
    
    try:
        db_agent_tools = MediaCrawlerDB()
        logger.info("数据库工具初始化成功，开始执行测试场景...\n")
        
        # 场景1: (新) 查找过去一周综合热度最高的内容 (不再需要sort_by)
        response1 = db_agent_tools.search_hot_content(time_period='week', limit=5)
        print_response_summary(response1)

        # 场景2: 查找过去24小时内综合热度最高的内容
        response2 = db_agent_tools.search_hot_content(time_period='24h', limit=5)
        print_response_summary(response2)

        # 场景3: 全局搜索"罗永浩"
        response3 = db_agent_tools.search_topic_globally(topic="罗永浩", limit_per_table=2)
        print_response_summary(response3)

        # 场景4: (新增) 在B站上精确搜索"论文"
        response4 = db_agent_tools.search_topic_on_platform(platform='bilibili', topic="论文", limit=5)
        print_response_summary(response4)

        # 场景5: (新增) 在微博上精确搜索 "许凯" 在特定一天内的内容
        response5 = db_agent_tools.search_topic_on_platform(platform='weibo', topic="许凯", start_date='2025-08-22', end_date='2025-08-22', limit=5)
        print_response_summary(response5)

    except ValueError as e:
        logger.exception(f"初始化失败: {e}")
        logger.exception("请确保相关的数据库环境变量已正确设置, 或在代码中直接提供连接信息。")
    except Exception as e:
        logger.exception(f"测试过程中发生未知错误: {e}")
