from lineage.query_context import QueryContext
from lineage.table_resolver import TableResolver
from lineage.query import Query
from lineage.utils import get_logger
import re
import sqlfluff

logger = get_logger(__name__)

DOLLAR_SIGN_REGEX = re.compile(r'(\b\S+)(\$)(\S+\b)')
DOLLAR_SIGN_PLACEHOLDER = '__dollar_sign__'


class SnowflakeQuery(Query):
    PLATFORM_TYPE = 'SNOWFLAKE'

    @staticmethod
    def from_dict(query_dict: dict):
        query_context = QueryContext.from_dict(query_dict.pop('query_context'))
        if 'platform_type' in query_dict:
            query_dict.pop('platform_type')
        return SnowflakeQuery(**query_dict, query_context=query_context)

    @staticmethod
    def replace_dollar_sign_with_placeholder(query_text: str) -> str:
        return re.sub(DOLLAR_SIGN_REGEX, f'\\1{DOLLAR_SIGN_PLACEHOLDER}\\3', query_text)

    @staticmethod
    def revert_dollar_sign_placeholder(name: str) -> str:
        return name.replace(DOLLAR_SIGN_PLACEHOLDER, '$')

    @staticmethod
    def _parse_merge_query(table_resolver: TableResolver, raw_query_text: str) -> (set, set):
        source_tables = set()
        target_tables = set()
        parsed_query = sqlfluff.parse(raw_query_text, dialect='snowflake')
        merge_stmts = list(parsed_query.tree.recursive_crawl('merge_statement'))
        if len(merge_stmts) != 1:
            return source_tables, target_tables
        merge_stmt = merge_stmts[0]
        look_for_target = False
        look_for_source = False
        for seg in merge_stmt.segments:
            if seg.is_type('keyword'):
                if seg.raw.lower() == 'into':
                    look_for_target = True
                    look_for_source = False
                elif seg.raw.lower() == 'using':
                    look_for_source = True
                    look_for_target = False
                else:
                    look_for_target = False
                    look_for_source = False
                continue

            if seg.is_type('table_reference') and look_for_target:
                table_name = table_resolver.name_qualification(seg.raw)
                target_tables.add(table_name)

            if look_for_source:
                sources = list(seg.recursive_crawl('table_reference'))
                for source in sources:
                    source_tables.add(table_resolver.name_qualification(source.raw))

        return source_tables, target_tables

    @classmethod
    def _parse_platform_specific_queries(cls, table_resolver: TableResolver, raw_query_text: str) -> (set, set):
        source_tables = set()
        target_tables = set()
        try:
            source_tables, target_tables = cls._parse_merge_query(table_resolver, raw_query_text)
        except Exception as exc:
            logger.debug(f'Exception was raised while parsing this query with sqlfluff -\n{raw_query_text}\n'
                         f'Error was -\n{exc}.')
        return source_tables, target_tables

    def parse(self, full_table_names: bool = False) -> bool:
        try:
            table_resolver = TableResolver(self._profile_database_name, self._profile_schema_name,
                                           self.query_context.queried_database, self.query_context.queried_schema,
                                           full_table_names, self.revert_dollar_sign_placeholder)

            # sqlparse library doesn't behave nicely when there is a $ sign in the table name. Therefore we replace it
            # with a placeholder (and revert it back later on using our table resolver)
            raw_query_text = self.replace_dollar_sign_with_placeholder(self._raw_query_text)
            self.source_tables, self.target_tables, self.renamed_tables, self.dropped_tables = \
                self._parse_query_text(table_resolver, raw_query_text)

            return True

        except Exception as exc:
            logger.debug(f'Exception was raised while parsing this query -\n{self._raw_query_text}\n'
                         f'Error was -\n{exc}.')
        return False

