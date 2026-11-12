# flake8: noqa  (a data list executed by i18n/build_ru.py, which supplies t, T, MI and the rest)
MI = "{mi|plural:миля,мили,миль}"
MI_GEN = "{mi|plural:мили,миль,миль}"
MI_ACC = "{mi|plural:милю,мили,миль}"  # после «через»: через 1 милю, 21 милю, 2 мили, 0,5 мили
MI_PREP = "{mi|plural:миле,милях,милях,мили}"
T2 = []
def t(en, ru, part=False):
    T2.append({"en": en, "ru": ru, **({"part": True} if part else {})})

# --- weather ---
t("Weather warning: {event} ahead near {area}.", "Погодное предупреждение: впереди {event|nom} в районе {area}.")
t("Weather warning: {event} near {area}, you'd hit it around {hit}{till}.", "Погодное предупреждение: {event|nom} в районе {area}, доедешь туда примерно в {hit|clock}{till|part}.")
t("Weather warning: {event} near {area}, you'd hit it around {hit}.", "Погодное предупреждение: {event|nom} в районе {area}, доедешь туда примерно в {hit|clock}.")
t("{event} and {n} more weather alert along the route.", "{event|nom} и ещё {n} погодное предупреждение по маршруту.")
t("{event} and {n} more weather alerts along the route.", "{event|nom} и ещё {n|plural:предупреждение,предупреждения,предупреждений} по маршруту.")
t("{event} near {area} around {hit} and {n} more weather alert along the route.", "{event|nom} в районе {area} около {hit|clock} и ещё {n} погодное предупреждение по маршруту.")
t("{event} near {area} around {hit} and {n} more weather alerts along the route.", "{event|nom} в районе {area} около {hit|clock} и ещё {n|plural:предупреждение,предупреждения,предупреждений} по маршруту.")
t("The way via {via} misses it{diff}. Say 'take the other way' if you want it.", "Путь через {via|and} это обходит{diff|tr}. Скажи «давай другим путём», если хочешь.")
t("The way via {via} misses it{diff}.", "Путь через {via|and} это обходит{diff|tr}.")
t("The other way misses it{diff}. Say 'take the other way' if you want it.", "Другой путь это обходит{diff|tr}. Скажи «давай другим путём», если хочешь.")
t("The way via {via} only runs into a {event}{diff}. Say 'take the other way' if you want it.", "На пути через {via|and} только {event|nom}{diff|part}. Скажи «давай другим путём», если хочешь.")
t("Say 'take the other way' if you want it.", "Скажи «давай другим путём», если хочешь.")
# {diff|part} above, not {diff|tr}: an open {diff} took the event's own words and the line never matched (the model
# translated it, or it stayed English). SAME_TIME had no fragment at all.
t(" and takes about the same time", " и по времени примерно так же", True)
t("No truck route around it.", "Объезда для трака нет.")
t("I don't have another truck route to try.", "Другого маршрута для трака у меня нет.")
t("No truck route around it; it should clear by {till}.", "Объезда для трака нет; должно закончиться к {till|clock}.")
t("Heads up, a {event} near {area} starts {onset}; at this pace you're through there by {hit}.", "Внимание, {event|nom} в районе {area} начнётся {onset|clock}; в таком темпе проедешь там к {hit|clock}.")
t("{place} {when}: {temp} degrees and {forecast}{wind}.", "{place} {when|clock}: {temp|plural:градус,градуса,градусов} по Фаренгейту, {forecast|tr}{wind|tr}.")
t("Heads up, there's a {event} in effect there.", "Внимание, там действует {event|nom}.")
t("I couldn't get a forecast for {place} right now.", "Прогноз для {place} сейчас получить не удалось.")
t(", wind {n} miles an hour from the {dir}", ", ветер {n|plural:миля,мили,миль} в час, {dir|from}", True)
t(", wind {n} miles an hour from the {dir}, gusting {g}", ", ветер {n|plural:миля,мили,миль} в час, {dir|from}, порывы до {g}", True)
t(", wind {n} miles an hour", ", ветер {n|plural:миля,мили,миль} в час", True)
t(", calm", ", штиль", True)

# --- chains ---
t("Chains or snow tires required on {route} near {location}.", "На {route} у {location} нужны цепи или зимняя резина.")
t("Heads up, chains required on {route} near {location}.", "Внимание, на {route} у {location} нужны цепи.")
t("Heads up, chains required on every vehicle, {route} near {location}.", "Внимание, цепи нужны всем, {route} у {location}.")
t("And {n} more chain control along the route.", "И ещё в одном месте по маршруту нужны цепи.")
t("And {n} more chain controls along the route.", "И ещё в {n|plural:месте,местах,местах} по маршруту нужны цепи.")
t("Set a route and I'll check Caltrans chain controls along it.", "Задай маршрут, и я проверю по Caltrans, где на нём нужны цепи.")
t("I only have chain controls for California, and this route doesn't go through it.", "Где нужны цепи, я знаю только по Калифорнии, а этот маршрут через неё не идёт.")
t("No chain controls on your route right now.", "Сейчас цепи на маршруте нигде не нужны.")
t("I can't check chain controls on your route right now. Try again in a minute.", "Сейчас не могу проверить, нужны ли на маршруте цепи. Попробуй через минуту.")
t("I can't check chain controls on your route right now.", "Сейчас не могу проверить, нужны ли на маршруте цепи.")
t("Chains required ahead on {route} near {location}. Do you have chains on board?", "Впереди на {route} у {location} нужны цепи. Цепи у тебя есть?")
t("Chains required ahead on {route} near {location}.", "Впереди на {route} у {location} нужны цепи.")
t("Do you have chains on board?", "Цепи у тебя есть?")
t("Got it, no chains on board. The last truck stop before the control is {name}, {mi} miles ahead, want me to add it?", "Понял, цепей нет. Последний трак-стоп до участка с цепями: {name}, через " + MI_ACC + ", добавить?")
t("The last truck stop before the control is {name}, {mi} miles ahead, want me to add it?", "Последний трак-стоп до участка с цепями: {name}, через " + MI_ACC + ", добавить?")
t("Got it, no chains on board. I couldn't find a truck stop before the control, keep an eye out for a safe spot.", "Понял, цепей нет. Трак-стоп до участка с цепями не нашёл, присматривай безопасное место.")
t("I couldn't find a truck stop before the control, keep an eye out for a safe spot.", "Трак-стоп до участка с цепями не нашёл, присматривай безопасное место.")
t("Chains required ahead on {route} near {location}, and you've got no chains on board. The last truck stop before it is {name}, {mi} miles ahead, want me to add it?", "Впереди на {route} у {location} нужны цепи, а у тебя их нет. Последний трак-стоп перед этим: {name}, через " + MI_ACC + ", добавить?")
t("Chains required ahead on {route} near {location}, and you've got no chains on board.", "Впереди на {route} у {location} нужны цепи, а у тебя их нет.")
t("The last truck stop before it is {name}, {mi} miles ahead, want me to add it?", "Последний трак-стоп перед этим: {name}, через " + MI_ACC + ", добавить?")
t("Got it, chains on board.", "Понял, цепи есть.")
t("Got it, no chains on board.", "Понял, цепей нет.")

# --- nudges / HOS ---
t("Your break is due in {due} minutes. There's a {name} in {mi} miles, want me to add it?", "Перерыв через {due|plural:минуту,минуты,минут}. Через " + MI_ACC + " есть {name}, добавить?")
t("Your break is due in {due} minutes.", "Перерыв через {due|plural:минуту,минуты,минут}.")
t("Your break is due now. There's a {name} in {mi} miles, want me to add it?", "Пора на перерыв. Через " + MI_ACC + " есть {name}, добавить?")
t("Your break is due now.", "Пора на перерыв.")
t("There's a {name} in {mi} miles, want me to add it?", "Через " + MI_ACC + " есть {name}, добавить?")
t("About {shift} minutes on the shift. The {name} in {mi} miles{live}, should I route you there?", "На смене осталось около {shift|plural:минуты,минут,минут}. {name} через " + MI_ACC + "{live|part}, проложить туда?")
t("About {shift} minutes on the shift.", "На смене осталось около {shift|plural:минуты,минут,минут}.")
t("It's getting late. The {name} in {mi} miles{live}, should I route you there?", "Уже поздно. {name} через " + MI_ACC + "{live|part}, проложить туда?")
t("It's getting late.", "Уже поздно.")
t("The {name} in {mi} miles{live}, should I route you there?", "{name} через " + MI_ACC + "{live|part}, проложить туда?")
t("Connect Motive in settings and I'll keep track of your hours.", "Подключи Motive в настройках, и я буду следить за твоими часами.")
t("Motive didn't accept the key. Check it in settings.", "Motive не принял ключ. Проверь его в настройках.")
t("Motive didn't accept the key.", "Motive не принял ключ.")
t("Check it in settings.", "Проверь его в настройках.")
t("I can't reach Motive right now. Try again in a minute.", "Сейчас не могу достучаться до Motive. Попробуй через минуту.")
t("I can't reach Motive right now.", "Сейчас не могу достучаться до Motive.")
t("Only {m} minutes of drive time left, let's find you a place to stop.", "Осталось всего {m|plural:минута,минуты,минут} за рулём, давай найдём, где встать.")
t("You've got {drive} of drive time, {shift} on the shift, and {cycle} left on the cycle.", "За рулём осталось {drive|time}, на смене {shift|time}, по циклу {cycle|time}.")
t("You're out of drive time. Let's find you a place to stop.", "Время за рулём вышло. Давай найдём, где встать.")

# --- parking / approach ---
t("Let me look at the satellite.", "Сейчас посмотрю по спутнику.")
t("Still looking at the satellite, I'll tell you when I have it.", "Ещё смотрю по спутнику, скажу, когда будет.")
t("They list truck parking there.", "Там указана парковка для траков.")
t("From the satellite, it looks like your rig will fit.", "По спутнику похоже, что твой трак поместится.")
t("Heads up, from the satellite it looks tight for a rig.", "Внимание, по спутнику там тесно для трака.")
t("I can't tell for sure from the satellite if a rig fits.", "По спутнику не могу точно сказать, поместится ли трак.")
t("The truck entrance is {tip}.", "Въезд для траков {tip|mt}.")
t("To get in: {tip}.", "Как заехать: {tip|mt}.")
t("on your {side}, off {street}", "{side|side}, с {street}", True)
t("on your {side}, off {street}, {rest}", "{side|side}, с {street}, {rest|mt}", True)
t("On your {side}, off {street}", "{side|side_cap}, с {street}", True)
t("{n} truck spots open there right now.", "Там сейчас свободно {n|plural:место,места,мест} для траков.")
t("Parking's almost full there.", "Парковка там почти заполнена.")
t("Parking's full there right now.", "Парковка там сейчас заполнена.")
t("{cap} truck spots there, no live count.", "Там {cap|plural:место,места,мест} для траков, живого счётчика нет.")
t("Ask for a truck stop or give an address to check the parking.", "Спроси трак-стоп или дай адрес, и я проверю парковку.")
# NearbyLot.phrase (models.py): spoken as a sentence, on the card as the reason's clause after "; "
t("There's a lot with trailers to the {dir}{look}.", "Есть площадка с трейлерами {dir|at}{look|part}.")
t("there's a lot with trailers to the {dir}{look}.", "есть площадка с трейлерами {dir|at}{look|part}.")
t(", looks like a public lot", ", похоже, общая стоянка", True)
t(", looks like a private yard", ", похоже, частная площадка", True)

# --- reviews ---
t("Drivers say: {gist}", "Водители говорят: {gist|mt}")
t("No driver reviews there yet.", "Отзывов водителей там пока нет.")
t("Drivers rate it {rating} out of 5, but I can't read the reviews right now.", "Водители ставят {rating} из 5, но прочитать отзывы сейчас не могу.")
t("Drivers rate it {rating} out of 5 on Google{count}. The reviews are on the place card.", "Водители ставят {rating} из 5 на Google{count|tr}. Отзывы есть на карточке места.")
t(", from {n} reviews", ", по {n|plural:отзыву,отзывам,отзывам}", True)

# --- profile / memory ---
t("Here's what I know. {facts}", "Вот что я знаю. {facts|tr}")
t("Here's what I know.", "Вот что я знаю.")
t("From your profile: {prefs}.", "Из твоего профиля: {prefs|tr}.")
t("I don't know anything about you yet. Tell me what you like and I'll remember.", "Я о тебе пока ничего не знаю. Расскажи, что тебе нравится, и я запомню.")
t("I don't know anything about you yet.", "Я о тебе пока ничего не знаю.")
t("Tell me what you like and I'll remember.", "Расскажи, что тебе нравится, и я запомню.")
t("Done, I've forgotten what I'd learned about you.", "Готово, всё, что узнал о тебе, забыл.")
t("Got it, I'll remember {what}.", "Понял, запомню: {what|tr}.")
t("Got it, I'll stop suggesting {chain}.", "Понял, больше не буду предлагать {chain}.")
t("You've stopped at {places}.", "Ты останавливался в {places|tr}.")
t("{name} {n} times", "{name} {n|plural:раз,раза,раз}", True)
t("{name} once", "{name} один раз", True)
t("You like {x}.", "Тебе нравится {x|and}.")
t("You'd rather skip {x}.", "Ты не любишь {x|and}.")
t("You avoid {x}.", "Ты избегаешь {x|tr}.")
t("You like short answers.", "Ты любишь короткие ответы.")
t("You stop for the night at {x}.", "На ночь ты встаёшь на {x|tr}.")
t("you like {x}", "тебе нравится {x|and}", True)
t(", you like {x}", ", тебе нравится {x|and}", True)
t(", you avoid {x}", ", ты избегаешь {x|tr}", True)
t(", you'd rather skip {x}", ", ты не любишь {x|and}", True)
t(", you stop for the night at {x}", ", на ночь встаёшь на {x|tr}", True)
t("you avoid {x}", "ты избегаешь {x|tr}", True)
t("toll roads", "платных дорог", True)
t("tolls", "платных дорог", True)
t("truck stops", "трак-стопах", True)

# --- controls ---
for en, ru in [("There's no route yet. Where do you want to go?", "Маршрута пока нет. Куда едем?"),
               ("We're already rolling.", "Мы уже едем."), ("Alright, let's roll.", "Хорошо, поехали."),
               ("Okay, route ended.", "Хорошо, маршрут завершён."), ("There's no active route right now.", "Сейчас активного маршрута нет."),
               ("Here are the other options.", "Вот другие варианты."), ("I don't have other options right now.", "Других вариантов сейчас нет."),
               ("Here's the whole route.", "Вот весь маршрут."), ("There's no route to show yet.", "Показывать пока нечего, маршрута нет."),
               ("Back on you.", "Снова на тебе."), ("Zooming in.", "Приближаю."), ("Zooming out.", "Отдаляю."),
               ("Okay, I'll call out the turns.", "Хорошо, буду подсказывать повороты."), ("Okay, turn directions are muted.", "Хорошо, подсказки поворотов выключены."),
               ("Hands-free listening isn't available in this version yet. Tap the copilot when you need me.", "В этой версии слушать без кнопки я пока не умею. Нажми на меня, когда понадоблюсь."),
               ("Okay. Tap the copilot when you need me.", "Хорошо. Нажми на меня, когда понадоблюсь."),
               ("That height doesn't look right. It has to be between 5 feet and 16 foot 11.", "Высота какая-то не та. Она должна быть от 5 футов до 16 футов 11 дюймов."),
               ("The trailer has to be between 10 and 65 feet.", "Трейлер должен быть от 10 до 65 футов."),
               ("The weight has to be between 5,000 and 200,000 pounds.", "Вес должен быть от 5\u00a0000 до 200\u00a0000 фунтов."),
               ("Hazmat is on, I'll keep you on hazmat-legal roads.", "Hazmat включён, поведу по дорогам, где hazmat разрешён."),
               ("Hazmat is off.", "Hazmat выключен."), ("I'll avoid toll roads.", "Буду объезжать платные дороги."),
               ("Toll roads are fine now.", "Платные дороги теперь можно."), ("I'll avoid ferries and tunnels.", "Буду объезжать паромы и тоннели."),
               ("Ferries and tunnels are fine now.", "Паромы и тоннели теперь можно."),
               ("You already have eight favorites. Remove one first.", "У тебя уже восемь любимых трак-стопов. Сначала убери один."),
               ("Break reminders are off.", "Напоминания о перерыве выключены."), ("Break reminders can be every 1 to 14 hours.", "Напоминать о перерыве можно раз в 1–14 часов."),
               ("Chains on board, yes or no?", "Цепи есть, да или нет?"), ("Suggestions on or off?", "Подсказки включить или выключить?"),
               ("I'll speak up with suggestions again.", "Снова буду подсказывать."), ("Okay, I'll stop suggesting things.", "Хорошо, больше не буду ничего предлагать."),
               ("Rerouting for that.", "Перестраиваю маршрут.")]:
    t(en, ru)
t("Height is now {ft} foot {inch}.", "Высота теперь {ft|plural:фут,фута,футов} {inch|plural:дюйм,дюйма,дюймов}.")
t("Height is now {ft} feet.", "Высота теперь {ft|plural:фут,фута,футов}.")
t("Trailer is now {n} feet.", "Трейлер теперь {n|plural:фут,фута,футов}.")
t("Weight is now {n} pounds.", "Вес теперь {n|plural:фунт,фунта,фунтов}.")
t("Got it, I'll call you {name}.", "Понял, буду звать тебя {name}.")
t("{name} is already a favorite.", "{name} уже в любимых.")
t("Added {name} to your favorites.", "Добавил {name} в любимые.")
t("Removed {name} from your favorites.", "Убрал {name} из любимых.")
t("{name} isn't in your favorites.", "{name} нет в любимых.")
t("I'll remind you to take a break every {n} hours.", "Буду напоминать о перерыве каждые {n|plural:час,часа,часов}.")
t("I'll remind you to take a break every {n} hour.", "Буду напоминать о перерыве каждый {n} час.")
t("Done, {items}.", "Готово: {items|tr}.")
t("Done. {rest}", "Готово. {rest|tr}")
for en, ru in [(", height is now {ft} foot {inch}", ", высота теперь {ft|plural:фут,фута,футов} {inch|plural:дюйм,дюйма,дюймов}"),
               (", height is now {ft} feet", ", высота теперь {ft|plural:фут,фута,футов}"),
               (", trailer is now {n} feet", ", трейлер теперь {n|plural:фут,фута,футов}"),
               (", weight is now {n} pounds", ", вес теперь {n|plural:фунт,фунта,фунтов}"),
               (", hazmat is on, I'll keep you on hazmat-legal roads", ", hazmat включён, поведу по дорогам, где он разрешён"),
               (", hazmat is off", ", hazmat выключен"), (", I'll avoid toll roads", ", буду объезжать платные дороги"),
               (", toll roads are fine now", ", платные дороги теперь можно"), (", I'll avoid ferries and tunnels", ", буду объезжать паромы и тоннели"),
               (", ferries and tunnels are fine now", ", паромы и тоннели теперь можно"), (", got it, chains on board", ", цепи есть"),
               (", got it, no chains on board", ", цепей нет"), (", break reminders are off", ", напоминания о перерыве выключены"),
               (", got it, I'll call you {name}", ", буду звать тебя {name}"), (", added {name} to your favorites", ", добавил {name} в любимые"),
               (", removed {name} from your favorites", ", убрал {name} из любимых"),
               (", I'll remind you to take a break every {n} hours", ", буду напоминать о перерыве каждые {n|plural:час,часа,часов}")]:
    t(en, ru, True)
t("height is now {ft} foot {inch}", "высота теперь {ft|plural:фут,фута,футов} {inch|plural:дюйм,дюйма,дюймов}", True)
t("height is now {ft} feet", "высота теперь {ft|plural:фут,фута,футов}", True)

# --- breakdown ---
t("Okay. Give me a minute, I'll check who's around and what drivers say about them.", "Хорошо. Дай минуту, посмотрю, кто есть рядом и что о них говорят водители.")
t("Give me a minute, I'll check who's around and what drivers say about them.", "Дай минуту, посмотрю, кто есть рядом и что о них говорят водители.")
t("They don't mention that, ask when you call.", "Об этом они не пишут, спроси, когда будешь звонить.")
t("I couldn't find anyone nearby who works on trucks. Want me to look for a tow instead?", "Рядом никого, кто работает с траками, не нашёл. Поискать эвакуатор?")
t("I couldn't pull reviews, here's who's nearby.", "Отзывы получить не удалось, вот кто есть рядом.")
t("None of them say they come out to you, so you'd have to get to them.", "Никто из них не пишет, что выезжает, так что придётся доехать самому.")
t("Tap one to call, or ask me anything about them.", "Нажми, чтобы позвонить, или спроси меня о них.")
t("Sorry, the search took too long. Want me to try again?", "Извини, поиск занял слишком долго. Попробовать ещё раз?")
t("Which tire, steer, drive or trailer? And can you roll on it or you need them to come out?", "Какое колесо: рулевое, ведущее или на трейлере? И доедешь сам или нужен выезд?")
t("Which tire, steer, drive or trailer?", "Какое колесо: рулевое, ведущее или на трейлере?")
t("And can you roll on it or you need them to come out?", "И доедешь сам или нужен выезд?")
t("Can it still drive, or does it need to come to you?", "Ехать ещё можешь или нужен выезд к тебе?")
t("Okay, I'll look for a shop you can roll to.", "Хорошо, поищу мастерскую, до которой доедешь.")
t("Okay, I'll look for someone who can come out to you.", "Хорошо, поищу тех, кто выезжает к тебе.")
t("Okay, I'll look for someone who can come out to you, tell them which tire when you call.", "Хорошо, поищу тех, кто выезжает к тебе, а какое колесо, скажешь им по телефону.")
t("Nobody posts prices, you'll have to ask. Tap one to call.", "Цен никто не пишет, придётся спросить. Нажми, чтобы позвонить.")
t("Tap one to call.", "Нажми, чтобы позвонить.")
t("That's everyone I found who works on trucks around here. Want me to look for a tow instead?", "Это все, кто работает с траками поблизости. Поискать эвакуатор?")
t("None of them mention {what}. Want me to call {name}, the top pick?", "Никто из них не упоминает {what}. Позвонить {name}, лучшему варианту?")
t("Calling {name}.", "Звоню {name}.")
t("Sorry, I lost that. Tell me again what happened?", "Извини, потерял нить. Расскажи ещё раз, что случилось?")

# --- places messages ---
t("No demo places here. Demo data covers the Bay Area and Sacramento.", "Здесь демо-мест нет. Демо-данные есть только по району Сан-Франциско и Сакраменто.")
t("I don't have {what} data loaded yet.", "Данных по {what|dat} у меня пока нет.")

W = {
    "truck stop": {"nom": "трак-стоп", "acc": "трак-стоп", "gen": "трак-стопа", "dat": "трак-стопам", "nom_pl": "Трак-стопы"},
    "rest area": {"nom": "рест-эрия", "acc": "рест-эрию", "gen": "рест-эрии", "dat": "рест-эриям", "nom_pl": "Рест-эрии"},
    "hotel": {"nom": "мотель", "acc": "мотель", "gen": "мотеля", "dat": "мотелям", "nom_pl": "Мотели"},
    "motel": {"nom": "мотель", "acc": "мотель", "gen": "мотеля", "dat": "мотелям", "nom_pl": "Мотели"},
    "walmart": {"nom": "Walmart", "acc": "Walmart", "gen": "Walmart", "dat": "Walmart", "nom_pl": "Walmart"},
    "cat scale": {"nom": "кэт-скейл", "acc": "кэт-скейл", "gen": "кэт-скейла", "dat": "кэт-скейлам", "nom_pl": "Кэт-скейлы"},
    "truck wash": {"nom": "мойка", "acc": "мойку", "gen": "мойки", "dat": "мойкам", "nom_pl": "Мойки"},
    "weigh station": {"nom": "весовая", "acc": "весовую", "gen": "весовой", "dat": "весовым", "nom_pl": "Весовые"},
    "fuel stop": {"nom": "заправка", "acc": "заправку", "gen": "заправки", "dat": "заправкам", "nom_pl": "Заправки"},
    "height": {"dat": "высоте"}, "weight": {"dat": "весу"}, "length": {"dat": "длине"}, "width": {"dat": "ширине"},
    "east": {"from": "восточный", "at": "к востоку"}, "west": {"from": "западный", "at": "к западу"},
    "north": {"from": "северный", "at": "к северу"}, "south": {"from": "южный", "at": "к югу"},
    "northeast": {"from": "северо-восточный", "at": "к северо-востоку"},
    "northwest": {"from": "северо-западный", "at": "к северо-западу"},
    "southeast": {"from": "юго-восточный", "at": "к юго-востоку"},
    "southwest": {"from": "юго-западный", "at": "к юго-западу"},
    "north-northeast": {"from": "северо-северо-восточный"}, "east-northeast": {"from": "восточно-северо-восточный"},
    "east-southeast": {"from": "восточно-юго-восточный"}, "south-southeast": {"from": "юго-юго-восточный"},
    "south-southwest": {"from": "юго-юго-западный"}, "west-southwest": {"from": "западно-юго-западный"},
    "west-northwest": {"from": "западно-северо-западный"}, "north-northwest": {"from": "северо-северо-западный"},
    "left": {"side": "слева", "side_cap": "Слева"}, "right": {"side": "справа", "side_cap": "Справа"},
    "sunny": "солнечно", "mostly sunny": "в основном солнечно", "partly sunny": "переменная облачность",
    "clear": "ясно", "mostly clear": "в основном ясно", "partly cloudy": "переменная облачность",
    "mostly cloudy": "в основном облачно", "cloudy": "облачно", "rain": "дождь", "light rain": "небольшой дождь",
    "rain showers": "ливни", "chance rain showers": "возможны ливни", "slight chance rain showers": "небольшая вероятность ливней",
    "showers and thunderstorms": "ливни и грозы", "chance showers and thunderstorms": "возможны ливни и грозы",
    "snow": "снег", "light snow": "небольшой снег", "chance snow": "возможен снег", "snow showers": "снегопад",
    "fog": "туман", "patchy fog": "местами туман", "areas of fog": "местами туман", "windy": "ветрено",
    "breezy": "ветрено", "no details": "без подробностей", "rain and snow": "дождь со снегом",
    "high wind warning": "штормовое предупреждение о ветре", "high wind watch": "угроза сильного ветра",
    "wind advisory": "предупреждение о ветре", "extreme wind warning": "предупреждение об экстремальном ветре",
    "winter storm warning": "предупреждение о зимнем шторме", "winter storm watch": "угроза зимнего шторма",
    "winter weather advisory": "предупреждение о зимней погоде", "blizzard warning": "предупреждение о метели",
    "ice storm warning": "предупреждение о гололёде", "snow squall warning": "предупреждение о снежном шквале",
    "freezing rain advisory": "предупреждение о ледяном дожде", "freezing fog advisory": "предупреждение о ледяном тумане",
    "dense fog advisory": "предупреждение о густом тумане", "dust storm warning": "предупреждение о пыльной буре",
    "blowing dust advisory": "предупреждение о пыльном ветре", "dust advisory": "предупреждение о пыли",
    "flash flood warning": "предупреждение о внезапном наводнении", "flood warning": "предупреждение о наводнении",
    "extreme cold warning": "предупреждение о сильном морозе", "tornado warning": "предупреждение о торнадо",
    "severe thunderstorm warning": "предупреждение о сильной грозе", "hurricane warning": "предупреждение об урагане",
    "hurricane watch": "угроза урагана", "tropical storm warning": "предупреждение о тропическом шторме",
    "tropical storm watch": "угроза тропического шторма", "storm surge warning": "предупреждение о штормовом нагоне",
    "hurricane force wind warning": "предупреждение об ураганном ветре", "avalanche warning": "предупреждение о лавинах",
    "dense smoke advisory": "предупреждение о густом дыме",
}
for en, ru in [("Likely fits a truck (satellite check)", "Скорее всего, трак встанет (по спутнику)"),
               ("Unlikely to fit a truck (satellite check)", "Скорее всего, трак не встанет (по спутнику)"),
               ("Truck parking unclear", "Неясно, есть ли парковка для траков"), ("Truck parking listed", "Указана парковка для траков"),
               ("Truck parking listed, not visible on satellite", "Парковка для траков указана, но по спутнику не видна"),
               ("No parking check for a town", "Для города парковку не проверяю"), ("Satellite image unavailable.", "Спутниковый снимок недоступен."),
               ("Couldn't read the satellite image.", "Не удалось разобрать спутниковый снимок."),
               ("The place info lists truck parking.", "В данных о месте указана парковка для траков."),
               ("Daily satellite checks are used up; I'll check this one tomorrow.", "Проверки по спутнику на сегодня закончились; это место проверю завтра.")]:
    t(en, ru)

# --- QA 2026-11-09: lines that went to the model or stayed English ---
W["size"] = {"dat": "габаритам"}
# where am I (S17)
t("You're on {street} in {city}, {state}.", "Ты на {street}, это {city}, {state}.")
t("You're in {city}, {state}.", "Ты в {city}, {state}.")
t("I can't tell where we are right now.", "Сейчас не могу понять, где мы.")
# a cheaper way (S17)
t("There's a way {diff} that saves about ${saves}. Want it?", "Есть путь {diff|tr}, экономия около ${saves}. Поедем по нему?")
t("There's a way {diff} that saves about ${saves}.", "Есть путь {diff|tr}, экономия около ${saves}.")
t("{d} slower", "на {d|time} дольше", True)
t("{d} faster", "на {d|time} быстрее", True)
t("about as fast", "примерно за то же время", True)
t("Want it?", "Поедем по нему?")
t("Okay, staying on this one.", "Хорошо, остаёмся на этом.")
t("This is already the cheapest way.", "Это и так самый дешёвый путь.")
t("This is already the fastest way.", "Это и так самый быстрый путь.")
t("I can't price the routes right now. Try again in a minute.", "Сейчас не могу посчитать, сколько стоят маршруты. Попробуй через минуту.")
t("I can't price the routes right now.", "Сейчас не могу посчитать, сколько стоят маршруты.")
# the other way: "..., about $5 cheaper in fuel and tolls" (S17, ALT_REPLY)
t(", about ${usd} cheaper in fuel and tolls", ", примерно на ${usd} дешевле по топливу и платным дорогам", True)
t(", about ${usd} more in fuel and tolls", ", примерно на ${usd} дороже по топливу и платным дорогам", True)
# prices, chains nearby (S17)
t("I don't have a diesel price for {name}.", "Цены на дизель в {name} у меня нет.")
t("I don't see a {chain} nearby.", "Рядом не вижу {chain}.")
# route notices (S18 and the router's other lines)
t("The truck router is down, so this is a backup route: no turn prompts, follow the line on the map.",
  "Сервис маршрутов для траков сейчас не работает, это запасной маршрут: подсказок поворотов не будет, езжай по линии на карте.")
t("Tolls couldn't be fully avoided on this route.", "Все платные дороги на этом маршруте объехать не получилось.")
t("Route includes a ferry.", "На маршруте паром.")
t("A tunnel couldn't be avoided on this route.", "Тоннель на этом маршруте объехать не получилось.")
t("Check the route for restrictions.", "Проверь маршрут на ограничения.")
t("Your truck is over the legal {what} limit in this state ({limit}); that takes a permit.",
  "Твой трак больше законного лимита по {what|dat} в этом штате ({limit|tr}), нужен пермит.")
t("the posted value", "указано на знаке", True)
t("{n} lb", "{n|plural:фунт,фунта,фунтов}", True)
t("{n} lb", "{n|plural:фунт,фунта,фунтов}")
# breakdown (S7)
t("I couldn't find anyone nearby who comes out to you. The closest shop is {name}, about {mi} miles, but you'd need a tow to get there. Tap it to call, or ask me to look for a tow.",
  "Рядом не нашёл никого, кто выезжает к тебе. Ближайшая мастерская: {name}, около " + MI_GEN + ", но туда нужен эвакуатор. Нажми, чтобы позвонить, или попроси поискать эвакуатор.")
t("I couldn't find anyone nearby who comes out to you.", "Рядом не нашёл никого, кто выезжает к тебе.")
t("The closest shop is {name}, about {mi} miles, but you'd need a tow to get there.", "Ближайшая мастерская: {name}, около " + MI_GEN + ", но туда нужен эвакуатор.")
t("Tap it to call, or ask me to look for a tow.", "Нажми, чтобы позвонить, или попроси поискать эвакуатор.")
t("Nobody nearby says they come out. Closest shop: {name}, {mi} mi, needs a tow.", "Никто рядом не пишет, что выезжает. Ближайшая мастерская: {name}, " + MI + ", нужен эвакуатор.")
t("The closest is {name}, about {mi} miles.", "Ближайший: {name}, около " + MI_GEN + ".")
t("Okay, here's what I found.", "Так, вот что я нашёл.")
t("Best bet is {name}, about {mi} miles{rest}.", "Лучший вариант: {name}, около " + MI_GEN + "{rest|tr}.")
t(" and they come out to you", ", и они выезжают к тебе", True)
t(", {n} stars", ", {n} из 5", True)
t("Tap one to call, or ask me anything about them.", "Нажми, чтобы позвонить, или спроси меня о них.")
t("Nobody posts prices, you'll have to ask. Tap one to call, or ask me anything about them.",
  "Цен никто не пишет, придётся спросить. Нажми, чтобы позвонить, или спроси меня о них.")
t("Nobody posts prices, you'll have to ask.", "Цен никто не пишет, придётся спросить.")
t("Ask for the road call fee and their ETA.", "Спроси, сколько стоит выезд и когда приедут.")
# the call card: questions to ask, the tire, the truck (S7, models.TruckProfile.label)
t("What's your road call fee?", "Сколько стоит выезд?")
t("What's your ETA to me?", "Когда сможете ко мне приехать?")
t("Do you take Comchek or EFS?", "Принимаете Comchek или EFS?")
t("{pos} tire {size}", "{pos|tire}, {size}")
t("{pos} tire", "{pos|tire}")
for hz in ("", ", hazmat"):
    t("{h} tall, {len} ft trailer (~{total} ft total), {w} lb gross" + hz,
      "высота {h|tr}, трейлер {len|plural:фут,фута,футов} (всего около {total|plural:фута,футов,футов}), полная масса "
      "{w|plural:фунт,фунта,фунтов}" + (", опасный груз" if hz else ""))
W["steer"] = {"tire": "рулевое колесо"}
W["drive"] = {"tire": "ведущее колесо"}
W["trailer"] = {"tire": "колесо трейлера"}

# --- incidents on the route (HERE type and description; S10) ---
for typ, ru in [("accident", "Авария"), ("congestion", "Пробка"), ("construction", "Ремонт дороги"),
                ("disabledVehicle", "Сломанная машина"), ("massTransit", "Общественный транспорт"),
                ("plannedEvent", "Мероприятие"), ("roadHazard", "Опасность на дороге"), ("roadClosure", "Дорога закрыта"),
                ("weather", "Погода"), ("laneRestriction", "Сужение дороги"), ("other", "Происшествие")]:
    W[typ.lower()] = ru
for en, ru in [("Road construction", "Ремонт дороги"), ("Major road construction", "Большой ремонт дороги"),
               ("Road maintenance operations", "Дорожные работы"), ("Construction work", "Дорожные работы"),
               ("Roadworks", "Дорожные работы"), ("Accident", "Авария"), ("Multi-vehicle accident", "Авария с несколькими машинами"),
               ("Road closed", "Дорога закрыта"), ("Closed", "Закрыто"), ("Bridge closed", "Мост закрыт"),
               ("Ramp closed", "Съезд закрыт"), ("Exit closed", "Съезд закрыт"), ("Entry ramp closed", "Въезд закрыт"),
               ("Exit ramp closed", "Съезд закрыт"), ("Lane closed", "Полоса закрыта"), ("Right lane closed", "Правая полоса закрыта"),
               ("Left lane closed", "Левая полоса закрыта"), ("One lane closed", "Одна полоса закрыта"),
               ("Two lanes closed", "Две полосы закрыты"), ("Three lanes closed", "Три полосы закрыты"),
               ("Shoulder closed", "Обочина закрыта"), ("Hard shoulder closed", "Обочина закрыта"),
               ("Stationary traffic", "Стоячая пробка"), ("Slow traffic", "Медленное движение"), ("Queuing traffic", "Затор"),
               ("Heavy traffic", "Плотное движение"), ("Disabled vehicle", "Сломанная машина"),
               ("Broken down vehicle", "Сломанная машина"), ("Debris on road", "Мусор на дороге"),
               ("Object on roadway", "Предмет на дороге"), ("Vehicle fire", "Горит машина"), ("Flooding", "Подтопление"),
               ("Road closed due to construction", "Дорога закрыта на ремонт"), ("Closed due to accident", "Закрыто из-за аварии")]:
    t(en, ru)
    t(en + ".", ru + ".")
t("{n} lanes closed", "Закрыто полос: {n}")
t("{n} lanes closed.", "Закрыто полос: {n}.")

# --- Caltrans chain-control descriptions (S10; the app shows R-1..R-3 and these) ---
for en, ru in [("No chain controls are in effect at this time.", "Сейчас цепи нигде не нужны."),
               ("Chains or snow tires are required.", "Нужны цепи или зимняя резина."),
               ("Chains are required on all vehicles except 4-wheel drive vehicles with snow tires on all 4 wheels.",
                "Цепи нужны всем, кроме полноприводных машин с зимней резиной на всех колёсах."),
               ("Chains are required on all vehicles.", "Цепи нужны всем машинам."),
               ("Chains are required on all vehicles - no exceptions.", "Цепи нужны всем, без исключений.")]:
    t(en, ru)

# --- NWS events outside services/weather.EVENTS: a route can carry them (S10) ---
# A "... Watch" is a noun like the rest ("угроза зимнего шторма"), never a clause ("возможен зимний шторм"): every
# template puts the event where a noun goes, and the clause gave "Внимание, возможен зимний шторм в районе Lassen
# начнётся в пятницу" (QA 2026-11-11).
W.update({
    "winter storm warning": "предупреждение о зимнем шторме", "frost advisory": "предупреждение о заморозках",
    "freeze warning": "предупреждение о морозе", "freeze watch": "угроза заморозков", "hard freeze warning": "предупреждение о сильном морозе",
    "heat advisory": "предупреждение о жаре", "excessive heat warning": "предупреждение о сильной жаре",
    "extreme heat warning": "предупреждение о сильной жаре", "red flag warning": "пожароопасная погода",
    "fire weather watch": "угроза пожароопасной погоды", "lake effect snow warning": "предупреждение о сильном снегопаде",
    "wind chill warning": "предупреждение о ледяном ветре", "wind chill advisory": "предупреждение о холодном ветре",
    "cold weather advisory": "предупреждение о холоде", "extreme cold watch": "угроза сильного мороза",
    "flood advisory": "предупреждение о подтоплении", "flood watch": "угроза наводнения", "flash flood watch": "угроза внезапного наводнения",
    "severe thunderstorm watch": "угроза сильной грозы", "tornado watch": "угроза торнадо", "avalanche watch": "угроза лавин",
    "special weather statement": "особое сообщение о погоде", "air quality alert": "плохое качество воздуха",
})
