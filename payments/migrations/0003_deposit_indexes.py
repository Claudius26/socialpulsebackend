"""
Purely-additive performance indexes for Deposit.

Hand-written (not auto-generated) on purpose: `makemigrations` wanted to also
drop a drifted `channel` column and alter `provider_reference`. Those are
pre-existing model/migration drift and dropping a column is destructive, so this
migration intentionally contains ONLY the safe, additive index additions.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0002_banktransferaccount_deposit_channel_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="deposit",
            index=models.Index(fields=["user", "status"], name="payments_de_user_st_idx"),
        ),
        migrations.AddIndex(
            model_name="deposit",
            index=models.Index(fields=["user", "-created_at"], name="payments_de_user_ct_idx"),
        ),
    ]
