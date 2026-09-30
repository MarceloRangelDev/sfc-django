from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='segmento_trabalho',
            field=models.CharField(
                choices=[
                    ('servico', 'Serviço'),
                    ('produto', 'Produto'),
                    ('ambos', 'Serviço e Produto'),
                ],
                default='produto',
                help_text='Define se as regras de precificação usadas são as de serviço, de produto ou ambas.',
                max_length=10,
                verbose_name='Segmento de trabalho',
            ),
        ),
    ]
