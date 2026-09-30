from django.contrib import admin
from .models import Profile

# Register your models here.
class Member(admin.ModelAdmin):
    list_display = (
        "user",
        "email",
        "segmento_trabalho",
        "is_verified",
        "created_at",
    )
    list_filter = (
        "segmento_trabalho",
        "is_verified",
    )
    search_fields = (
        "user__username",
        "email",
    )


admin.site.register(Profile, Member)
