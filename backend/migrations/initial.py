from sqlalchemy import inspect, text
from src.config.database import Base, engine
from src import models


ADDED_COLUMNS = {
    'jobs': [
        ('metric_id', 'INTEGER NULL'),
        ('metric_version_id', 'INTEGER NULL'),
        ('metric_snapshot', 'JSON NULL'),
    ],
    'deliveries': [
        ('metric_snapshot', 'JSON NULL'),
    ],
}


def _add_missing_columns():
    """create_all creates new tables; this adds new nullable columns to old ones."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, columns in ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {column['name'] for column in inspector.get_columns(table)}
            for name, definition in columns:
                if name in present:
                    continue
                connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))


def migrate():
    Base.metadata.create_all(engine)
    _add_missing_columns()


if __name__ == '__main__':
    migrate()
