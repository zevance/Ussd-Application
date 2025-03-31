from django.db import models

# Create your models here.
class Farmer(models.Model):
    phone_number = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=100, null=True, blank=True)
    district = models.CharField(max_length=100, null=True, blank=True)
    area = models.CharField(max_length=100, null=True, blank=True)
    current_step = models.IntegerField(default=1)
    updated_at = models.DateField(auto_now=True)
    created_at = models.DateField(auto_now_add=True)

    def __str__(self):
        return self.phone_number