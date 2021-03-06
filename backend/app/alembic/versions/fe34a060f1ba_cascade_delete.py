"""cascade_delete

Revision ID: fe34a060f1ba
Revises: 8ec14b991bc5
Create Date: 2021-12-14 15:02:06.318653

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'fe34a060f1ba'
down_revision = '8ec14b991bc5'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('jobs_scan_id_fkey', 'jobs', type_='foreignkey')
    op.create_foreign_key(None, 'jobs', 'scans', ['scan_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('locations_scan_id_fkey', 'locations', type_='foreignkey')
    op.create_foreign_key(None, 'locations', 'scans', ['scan_id'], ['id'], ondelete='CASCADE')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'locations', type_='foreignkey')
    op.create_foreign_key('locations_scan_id_fkey', 'locations', 'scans', ['scan_id'], ['id'])
    op.drop_constraint(None, 'jobs', type_='foreignkey')
    op.create_foreign_key('jobs_scan_id_fkey', 'jobs', 'scans', ['scan_id'], ['id'])
    # ### end Alembic commands ###
