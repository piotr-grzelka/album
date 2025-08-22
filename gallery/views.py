from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from gallery.models import Album


@login_required
def home_view(request):

    albums = Album.objects.all()

    context = {
        'albums': albums
    }

    return render(request, 'gallery/home.html', context)

@login_required
def album_view(request, slug):

    album = Album.objects.get(slug=slug)
    photos = album.photo_set.all()

    context = {
        'album': album,
        'photos': photos
    }

    return render(request, 'gallery/album.html', context)