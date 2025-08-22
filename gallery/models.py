import uuid
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.text import slugify
import os
from io import BytesIO
from PIL import Image, ExifTags, ImageOps
from django.core.files.base import ContentFile
from django.db import models
from storages.backends.s3boto3 import S3Boto3Storage

def _make_json_safe(value):
    """Konwertuj wartość EXIF do typu zgodnego z JSON (rekurencyjnie)."""
    if isinstance(value, (int, float, str, type(None))):
        return value
    if isinstance(value, dict):
        return {k: _make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_make_json_safe(v) for v in value]
    try:
        # np. IFDRational → float
        return float(value)
    except Exception:
        return str(value)  # fallback na string

def _convert_to_degrees(value):
    """Konwertuje (stopnie, minuty, sekundy) z EXIF na float w stopniach."""
    d, m, s = value
    return float(d) + float(m) / 60.0 + float(s) / 3600.0


def extract_gps(exif_data):
    """Zwraca współrzędne GPS (lat, lon) w stopniach dziesiętnych lub None."""
    try:
        gps = exif_data.get("GPSInfo")
        if not gps:
            return None

        lat = _convert_to_degrees(gps["GPSLatitude"])
        if gps.get("GPSLatitudeRef") == "S":
            lat = -lat

        lon = _convert_to_degrees(gps["GPSLongitude"])
        if gps.get("GPSLongitudeRef") == "W":
            lon = -lon

        return lat, lon
    except Exception as e:
        print(e)
        return None

public_storage = S3Boto3Storage(bucket_name=os.getenv("AWS_PUBLIC_BUCKET"), querystring_auth=False)


class Album(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(null=True, blank=True, unique=True)
    description = models.TextField(null=True, blank=True)
    date_created = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    objects = models.Manager()

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super(Album, self).save(*args, **kwargs)

    class Meta:
        verbose_name = 'Album'
        verbose_name_plural = 'Albumy'
        ordering = ['-date_created']

def image_upload_to(instance, filename):
    ext = filename.split('.')[-1]
    return f"photos/{instance.album.slug}/{uuid.uuid4().hex}.{ext}"


class Photo(models.Model):
    album = models.ForeignKey("Album", on_delete=models.CASCADE)
    name = models.CharField(null=True, blank=True)
    original_image = models.ImageField(upload_to=image_upload_to)
    thumbnail = models.ImageField(
        upload_to=image_upload_to, storage=public_storage, null=True, blank=True
    )
    large = models.ImageField(
        upload_to=image_upload_to, storage=public_storage, null=True, blank=True
    )
    author = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    exif = models.JSONField(null=True, blank=True)

    date_created = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    objects = models.Manager()

    def save(self, *args, **kwargs):

        if not self.name:
            self.name = self.original_image.name

        creating = self._state.adding
        super().save(*args, **kwargs)

        if creating and self.original_image:
            self.process_images_and_exif()

    def process_images_and_exif(self):
        """Generowanie miniatur, dużego obrazu i EXIF"""

        self.original_image.open()
        img = Image.open(self.original_image)


        # === EXIF ===
        exif_data = {}
        try:
            raw_exif = img._getexif()
            if raw_exif:
                exif_data = {}
                for tag, value in raw_exif.items():
                    tag_name = ExifTags.TAGS.get(tag, tag)
                    if tag_name in ['MakerNote']:
                        continue

                    if tag_name == "GPSInfo":
                        gps_data = {}
                        for key in value:
                            gps_tag = ExifTags.GPSTAGS.get(key, key)
                            gps_data[gps_tag] = _make_json_safe(value[key])
                        exif_data["GPSInfo"] = gps_data
                    else:
                        exif_data[tag_name] = _make_json_safe(value)

        except Exception:
            pass
        self.exif = exif_data

        try:
            self.longitude, self.latitude = extract_gps(exif_data)
        except TypeError:
            pass

        img = ImageOps.exif_transpose(img)

        # === MINIATURKA ===
        # === MINIATURKA (256x256, crop ze środka) ===
        thumb = img.copy()

        # najpierw crop na kwadrat ze środka
        min_side = min(thumb.width, thumb.height)
        left = (thumb.width - min_side) // 2
        top = (thumb.height - min_side) // 2
        right = left + min_side
        bottom = top + min_side
        thumb = thumb.crop((left, top, right, bottom))

        # dopiero teraz resize do 256x256
        thumb = thumb.resize((256, 256), Image.LANCZOS)

        thumb_io = BytesIO()
        thumb.save(thumb_io, format="JPEG", quality=85)
        self.thumbnail.save(
            f"thumb_{uuid.uuid4().hex}.jpg",
            ContentFile(thumb_io.getvalue()),
            save=False
        )

        # === DUŻA WERSJA ===
        large = img.copy()
        if large.width > 2000:
            ratio = 2000 / float(large.width)
            new_height = int(float(large.height) * ratio)
            large = large.resize((2000, new_height), Image.LANCZOS)

        large_io = BytesIO()
        large.save(large_io, format="JPEG", quality=90)
        self.large.save(f"large_{uuid.uuid4().hex}.jpg", ContentFile(large_io.getvalue()), save=False)

        # zapisujemy aktualizacje pól
        super().save(update_fields=["thumbnail", "large", "exif", "latitude", "longitude"])

    class Meta:
        verbose_name = 'Zdjęcie'
        verbose_name_plural = 'Zdjęcia'
        ordering = ['-date_created']


@receiver(post_delete, sender=Photo)
def delete_file_from_s3(sender, instance, **kwargs):

    print("removing files from S3", end="")

    if instance.original_image:
        print(" original ", end="")
        instance.original_image.delete(save=False)

    if instance.thumbnail:
        print(" thumbnail ", end="")
        instance.thumbnail.delete(save=False)

    if instance.large:
        print(" large ", end="")
        instance.large.delete(save=False)

    print(" done")