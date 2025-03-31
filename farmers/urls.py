# urls.py
from django.urls import path
from .views import handle_ussd

app_name = 'farmers'
urlpatterns = [
    path('', handle_ussd, name='handle_ussd'),
]