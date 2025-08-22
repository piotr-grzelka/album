from django.contrib import admin, messages
from django.utils.safestring import mark_safe
from django.urls import path
from django import forms
from .models import Album, Photo
from django.shortcuts import render, redirect

class MultiFileInput(forms.FileInput):
    allow_multiple_selected = True

class MultiUploadForm(forms.Form):
    album = forms.ModelChoiceField(queryset=Album.objects.all())
    images = forms.FileField(
        widget=MultiFileInput(attrs={'multiple': True}),
        required=False,
        label="Wybierz pliki",
        help_text="Możesz wybrać wiele plików jednocześnie.",
    )
    images.widget.allow_multiple_selected = True

    def is_valid(self):
        super().is_valid()
        return True


@admin.register(Album)
class AlbumAdmin(admin.ModelAdmin):
    list_display = ['name', 'date_created', 'date_updated']

@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ['name', 'album', 'date_created', 'thumbnail_img']
    list_filter = ['album']

    change_list_template = "admin/photo_changelist.html"

    def thumbnail_img(self, obj):
        if not obj.thumbnail:
            return "Brak miniaturki"
        return mark_safe(f'<img src="{obj.thumbnail.url}" width="100" height="100" />')

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("multi-upload/", self.admin_site.admin_view(self.multi_upload_view), name="photo-multi-upload"),
        ]
        return custom_urls + urls

    def multi_upload_view(self, request):
        if request.method == "POST":
            form = MultiUploadForm(request.POST, request.FILES)
            files = request.FILES.getlist("images")

            if form.is_valid() and files:
                album = form.cleaned_data["album"]
                for f in files:
                    Photo.objects.create(
                        album=album,
                        original_image=f,
                        name=f.name,
                        author=request.user
                    )
                self.message_user(request, f"Dodano {len(files)} zdjęć do albumu {album}.", messages.SUCCESS)
                return redirect("..")
        else:
            form = MultiUploadForm()

        context = {
            "form": form,
            "opts": self.model._meta,
            "title": "Multi upload zdjęć",
        }
        return render(request, "admin/multi_upload.html", context)