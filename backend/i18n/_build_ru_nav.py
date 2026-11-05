T3 = []
def t(en, ru, part=False):
    T3.append({"en": en, "ru": ru, **({"part": True} if part else {})})
DIRS = [("left", "налево"), ("right", "направо"), ("slightly left", "плавно налево"), ("slightly right", "плавно направо"),
        ("sharp left", "резко налево"), ("sharp right", "резко направо")]
for en, ru in DIRS:
    E = en; R = ru
    t(f"Turn {E}", f"Поверни {R}")
    t(f"Turn {E} onto {{road}}", f"Поверни {R} на {{road}}")
    t(f"Turn {E} onto {{road}} toward {{to}}", f"Поверни {R} на {{road}} в сторону {{to}}")
    t(f"Turn {E} toward {{to}}", f"Поверни {R} в сторону {{to}}")
    t(f"Turn {E} and take ramp onto {{road}} toward {{to}}", f"Поверни {R} и заезжай на {{road}} в сторону {{to}}")
    t(f"Turn {E} and take ramp onto {{road}}", f"Поверни {R} и заезжай на {{road}}")
for side, ru in (("left", "левый"), ("right", "правый")):
    t(f"Keep {side}", f"Держись {'левее' if side=='left' else 'правее'}")
    t(f"Keep {side} toward {{to}}", f"Держись {'левее' if side=='left' else 'правее'} в сторону {{to}}")
    t(f"Keep {side} onto {{road}}", f"Держись {'левее' if side=='left' else 'правее'} на {{road}}")
    t(f"Keep {side} onto {{road}} toward {{to}}", f"Держись {'левее' if side=='left' else 'правее'} на {{road}} в сторону {{to}}")
    t(f"Take the {side} exit onto {{road}}", f"Уходи на {ru} съезд на {{road}}")
    t(f"Take the {side} exit onto {{road}} toward {{to}}", f"Уходи на {ru} съезд на {{road}} в сторону {{to}}")
    t(f"Take the {side} exit toward {{to}}", f"Уходи на {ru} съезд в сторону {{to}}")
    t(f"Take {side} ramp onto {{road}} toward {{to}}", f"Заезжай на {ru} съезд на {{road}} в сторону {{to}}")
    t(f"Take {side} ramp onto {{road}}", f"Заезжай на {ru} съезд на {{road}}")
t("Continue on {road}", "Продолжай по {road}")
t("Continue on {road} toward {to}", "Продолжай по {road} в сторону {to}")
t("Continue toward {to}", "Продолжай в сторону {to}")
t("Take exit {n} toward {to}", "Съезд {n} в сторону {to}")
t("Take exit {n} toward {to} onto {road}", "Съезд {n} в сторону {to} на {road}")
t("Take exit {n} onto {road}", "Съезд {n} на {road}")
t("Take exit {n} onto {road} toward {to}", "Съезд {n} на {road} в сторону {to}")
t("Take the exit toward {to} onto {road}", "Уходи на съезд в сторону {to} на {road}")
t("Take the exit toward {to}", "Уходи на съезд в сторону {to}")
t("Take the exit onto {road}", "Уходи на съезд на {road}")
t("Take ramp onto {road} toward {to}", "Заезжай на {road} в сторону {to}")
t("Take ramp onto {road}", "Заезжай на {road}")
t("Head toward {to}", "Езжай в сторону {to}")
t("Head {dir} on {road}", "Езжай на {dir|to} по {road}")
t("Head {dir} on {road} toward {to}", "Езжай на {dir|to} по {road} в сторону {to}")
t("Head {dir}", "Езжай на {dir|to}")
t("Arrive at {place}", "Прибытие: {place}")
t("Arrive at your destination", "Ты на месте")
t("Your destination is on the left", "Место назначения слева")
t("Your destination is on the right", "Место назначения справа")
t("Your destination is straight ahead", "Место назначения прямо")
t("Take the {nth} exit from roundabout onto {road}", "На круге {nth|ord} съезд на {road}")
t("Take the {nth} exit from roundabout onto {road} toward {to}", "На круге {nth|ord} съезд на {road} в сторону {to}")
t("Take the {nth} exit from roundabout", "На круге {nth|ord} съезд")
t("Pass {n} roundabouts and continue on {road}", "Проедь {n|plural:круг,круга,кругов} и продолжай по {road}")
t("Make a U-turn", "Развернись")
t("Make a U-turn onto {road}", "Развернись на {road}")
t("Take the ferry", "Заезжай на паром")
t("Follow the route", "Следуй по маршруту")
W3 = {
    "north": {"to": "север"}, "south": {"to": "юг"}, "east": {"to": "восток"}, "west": {"to": "запад"},
    "northeast": {"to": "северо-восток"}, "northwest": {"to": "северо-запад"}, "southeast": {"to": "юго-восток"},
    "southwest": {"to": "юго-запад"},
    "1st": {"ord": "первый"}, "2nd": {"ord": "второй"}, "3rd": {"ord": "третий"}, "4th": {"ord": "четвёртый"},
    "5th": {"ord": "пятый"}, "6th": {"ord": "шестой"},
}
