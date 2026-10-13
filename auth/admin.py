from django.contrib import admin
from .models import Profile

# Register your models here.
class Member(admin.ModelAdmin):
    list_display = (
        "user",
        "email",
        "cliente",
        "is_verified",
        "created_at",
    )
    list_filter = (
        "is_verified",
    )
    search_fields = (
        "user__username",
        "email",
    )


admin.site.register(Profile, Member)
