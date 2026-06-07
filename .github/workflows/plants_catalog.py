"""
SmartHydro Plant Catalog – Python mirror of public/plants.js
============================================================
GENERATED FILE — do not edit by hand. Run `scripts/build_plants_catalog.py`
to regenerate from the JS source of truth.

Used by ai_manager.py to enrich Gemini prompts with crop-specific agronomic
context: name_he, family_he, cycle_days, notes_he, per-stage pH/EC targets,
ec_critical, ph_critical.
"""

PLANTS = {
    "lettuce": {
        "name_he": "חסה",
        "name_en": "Lettuce",
        "emoji": "🥬",
        "family_he": "מורכבים",
        "cycle_days": 45,
        "notes_he": "תיזהר מ-EC מעל 1.2 — גורם לצריבת קצוות (tip burn). חסה אוהבת EC נמוך וטמפ' קרירה.",
        "ph_critical": [
            5.0,
            6.5
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    500,
                    700
                ],
                "ec_critical": 900
            },
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 14,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    800,
                    1200
                ],
                "ec_critical": 1500
            }
        ]
    },
    "iceberg_lettuce": {
        "name_he": "חסת אייסברג",
        "name_en": "Iceberg Lettuce",
        "emoji": "🥬",
        "family_he": "מורכבים",
        "cycle_days": 60,
        "notes_he": "דורשת טמפ' קרירה לראש הדוק. דומה לחסה רגילה אבל לוקח יותר זמן.",
        "ph_critical": [
            5.5,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    800,
                    1200
                ],
                "ec_critical": 1500
            }
        ]
    },
    "curly_lettuce": {
        "name_he": "חסה מסולסלת",
        "name_en": "Curly Lettuce",
        "emoji": "🥬",
        "family_he": "מורכבים",
        "cycle_days": 40,
        "notes_he": "זהה לחסה רגילה. יפה במגוון צבעי חסה במערכת.",
        "ph_critical": [
            5.0,
            6.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    800,
                    1200
                ],
                "ec_critical": 1500
            }
        ]
    },
    "spinach": {
        "name_he": "תרד",
        "name_en": "Spinach",
        "emoji": "🥬",
        "family_he": "אמרנטיים",
        "cycle_days": 40,
        "notes_he": "אוהב pH יחסית בסיסי. רגיש לחום מעל 25°C — יקפוץ למצב פריחה (bolting).",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    1800,
                    2300
                ],
                "ec_critical": 2800
            }
        ]
    },
    "kale": {
        "name_he": "קייל",
        "name_en": "Kale",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 55,
        "notes_he": "EC נמוך משחושבים. צמח עמיד שעובד טוב גם בטמפ' קרירה.",
        "ph_critical": [
            5.0,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    1200,
                    1500
                ],
                "ec_critical": 1900
            }
        ]
    },
    "arugula": {
        "name_he": "אורוגולה",
        "name_en": "Arugula",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 30,
        "notes_he": "גידול מהיר מאוד. EC גבוה מדי יחליש את הטעם החריף.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    800,
                    1200
                ],
                "ec_critical": 1500
            }
        ]
    },
    "bok_choy": {
        "name_he": "בוק צ'וי",
        "name_en": "Bok Choy",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 45,
        "notes_he": "כרוב סיני. אוהב טמפ' מתונה ולחות גבוהה.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    1500,
                    2000
                ],
                "ec_critical": 2500
            }
        ]
    },
    "swiss_chard": {
        "name_he": "מנגולד",
        "name_en": "Swiss Chard",
        "emoji": "🥬",
        "family_he": "אמרנטיים",
        "cycle_days": 50,
        "notes_he": "קרוב משפחה לתרד אבל עמיד יותר לחום.",
        "ph_critical": [
            5.5,
            7.2
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    6.6
                ],
                "ec": [
                    1800,
                    2300
                ],
                "ec_critical": 2800
            }
        ]
    },
    "beet_greens": {
        "name_he": "סלק עלים",
        "name_en": "Beet Greens",
        "emoji": "🥬",
        "family_he": "אמרנטיים",
        "cycle_days": 45,
        "notes_he": "עלי סלק במחזור קצר. דומה למנגולד בדרישות.",
        "ph_critical": [
            5.5,
            7.2
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    1800,
                    2300
                ],
                "ec_critical": 2800
            }
        ]
    },
    "mustard": {
        "name_he": "עלי חרדל",
        "name_en": "Mustard Greens",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 40,
        "notes_he": "טעם חריף. שלבי גידול קצרים — קטיף תוך 30-40 יום.",
        "ph_critical": [
            5.5,
            8.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.5
                ],
                "ec": [
                    1200,
                    2400
                ],
                "ec_critical": 2900
            }
        ]
    },
    "watercress": {
        "name_he": "גרגיר הנחלים",
        "name_en": "Watercress",
        "emoji": "🌱",
        "family_he": "מצליבים",
        "cycle_days": 50,
        "notes_he": "אוהב מים זורמים וקרירים. תוסיף חמצן למיכל.",
        "ph_critical": [
            6.0,
            7.3
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.5,
                    6.8
                ],
                "ec": [
                    400,
                    1800
                ],
                "ec_critical": 2200
            }
        ]
    },
    "tatsoi": {
        "name_he": "טטסוי",
        "name_en": "Tatsoi",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 45,
        "notes_he": "כרוב סיני עם עלים כפיים. דומה לבוק צ'וי, רק מתוק יותר.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    1500,
                    2200
                ],
                "ec_critical": 2700
            }
        ]
    },
    "mizuna": {
        "name_he": "מיזונה",
        "name_en": "Mizuna",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 40,
        "notes_he": "ירק יפני בסגנון חרדל. עלים מסולסלים, טעם עדין.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    1500,
                    2200
                ],
                "ec_critical": 2700
            }
        ]
    },
    "endive": {
        "name_he": "אנדייב",
        "name_en": "Endive",
        "emoji": "🥬",
        "family_he": "מורכבים",
        "cycle_days": 60,
        "notes_he": "צמיחה איטית, טעם מריר עדין. EC גבוה מחסה רגילה.",
        "ph_critical": [
            5.0,
            6.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    2000,
                    2400
                ],
                "ec_critical": 2900
            }
        ]
    },
    "celery": {
        "name_he": "סלרי",
        "name_en": "Celery",
        "emoji": "🥬",
        "family_he": "סוככיים",
        "cycle_days": 130,
        "notes_he": "מחזור גידול ארוך. דורש pH יחסית בסיסי וצריכת מים גבוהה.",
        "ph_critical": [
            5.8,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.5,
                    7.0
                ],
                "ec": [
                    1800,
                    2400
                ],
                "ec_critical": 3000
            }
        ]
    },
    "basil": {
        "name_he": "בזיליקום",
        "name_en": "Basil",
        "emoji": "🌿",
        "family_he": "שפתניים",
        "cycle_days": 30,
        "notes_he": "EC מתון שומר על שמנים אתריים וטעם חזק. רגיש לקור.",
        "ph_critical": [
            5.2,
            6.8
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    500,
                    800
                ],
                "ec_critical": 1100
            },
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 14,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    1000,
                    1600
                ],
                "ec_critical": 2000
            }
        ]
    },
    "cilantro": {
        "name_he": "כוסברה",
        "name_en": "Cilantro",
        "emoji": "🌿",
        "family_he": "סוככיים",
        "cycle_days": 50,
        "notes_he": "אוהבת טמפ' קרירה. בחום קופצת לפריחה תוך שבוע.",
        "ph_critical": [
            5.5,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.4
                ],
                "ec": [
                    1200,
                    1800
                ],
                "ec_critical": 2200
            }
        ]
    },
    "parsley": {
        "name_he": "פטרוזיליה",
        "name_en": "Parsley",
        "emoji": "🌿",
        "family_he": "סוככיים",
        "cycle_days": 70,
        "notes_he": "גידול איטי בהתחלה. אחרי שמתבסס נותן יבול במשך חודשים.",
        "ph_critical": [
            5.0,
            6.8
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    800,
                    1800
                ],
                "ec_critical": 2200
            }
        ]
    },
    "dill": {
        "name_he": "שמיר",
        "name_en": "Dill",
        "emoji": "🌿",
        "family_he": "סוככיים",
        "cycle_days": 50,
        "notes_he": "גידול מהיר. רגיש להפרעות שורש — מומלץ לזרוע ישירות.",
        "ph_critical": [
            5.0,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    1000,
                    1600
                ],
                "ec_critical": 2000
            }
        ]
    },
    "mint": {
        "name_he": "נענע",
        "name_en": "Mint",
        "emoji": "🌱",
        "family_he": "שפתניים",
        "cycle_days": 60,
        "notes_he": "צמח אגרסיבי — מתפזר מהר. רצוי לתת לו תא נפרד.",
        "ph_critical": [
            5.0,
            6.8
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    2000,
                    2400
                ],
                "ec_critical": 3000
            }
        ]
    },
    "chives": {
        "name_he": "עירית",
        "name_en": "Chives",
        "emoji": "🌱",
        "family_he": "נרקיסיים",
        "cycle_days": 80,
        "notes_he": "צמח רב-שנתי. EC גבוה דומה לבצל.",
        "ph_critical": [
            5.2,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    6.5
                ],
                "ec": [
                    1800,
                    2400
                ],
                "ec_critical": 2900
            }
        ]
    },
    "marjoram": {
        "name_he": "מיורן",
        "name_en": "Marjoram",
        "emoji": "🌿",
        "family_he": "שפתניים",
        "cycle_days": 60,
        "notes_he": "דומה לאורגנו, טעם עדין יותר. אוהב טמפ' חמה.",
        "ph_critical": [
            5.5,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    6.5
                ],
                "ec": [
                    1600,
                    2000
                ],
                "ec_critical": 2500
            }
        ]
    },
    "oregano": {
        "name_he": "אורגנו",
        "name_en": "Oregano",
        "emoji": "🌿",
        "family_he": "שפתניים",
        "cycle_days": 90,
        "notes_he": "צמיחה איטית. ככל שמתבסס, EC יכול לעלות.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    1800,
                    2300
                ],
                "ec_critical": 2800
            }
        ]
    },
    "lemon_balm": {
        "name_he": "מליסה",
        "name_en": "Lemon Balm",
        "emoji": "🌿",
        "family_he": "שפתניים",
        "cycle_days": 70,
        "notes_he": "ריח לימוני. עמיד וקל לגידול. רב-שנתי.",
        "ph_critical": [
            5.0,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    1000,
                    1600
                ],
                "ec_critical": 2000
            }
        ]
    },
    "lemon_verbena": {
        "name_he": "לואיזה",
        "name_en": "Lemon Verbena",
        "emoji": "🌿",
        "family_he": "ורבניים",
        "cycle_days": 90,
        "notes_he": "ריח לימוני עז. רגיש לקור — לא לחורף ללא חמום.",
        "ph_critical": [
            5.5,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    6.5
                ],
                "ec": [
                    1000,
                    1600
                ],
                "ec_critical": 2000
            }
        ]
    },
    "thyme": {
        "name_he": "טימין",
        "name_en": "Thyme",
        "emoji": "🌿",
        "family_he": "שפתניים",
        "cycle_days": 90,
        "notes_he": "צמח מים-תיכוני, אוהב קצת יובש. אל תציף שורשים.",
        "ph_critical": [
            5.0,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    7.0
                ],
                "ec": [
                    800,
                    1600
                ],
                "ec_critical": 2000
            }
        ]
    },
    "rosemary": {
        "name_he": "רוזמרין",
        "name_en": "Rosemary",
        "emoji": "🌿",
        "family_he": "שפתניים",
        "cycle_days": 90,
        "notes_he": "אוהב מעט מים ויובש בין השקיות. מתאים פחות למערכת רטובה תמיד.",
        "ph_critical": [
            5.0,
            6.8
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    1000,
                    1600
                ],
                "ec_critical": 2000
            }
        ]
    },
    "sage": {
        "name_he": "מרווה",
        "name_en": "Sage",
        "emoji": "🌿",
        "family_he": "שפתניים",
        "cycle_days": 75,
        "notes_he": "עלים אפרפרים. דומה לרוזמרין בדרישות.",
        "ph_critical": [
            5.0,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    1000,
                    1600
                ],
                "ec_critical": 2000
            }
        ]
    },
    "lavender": {
        "name_he": "לבנדר",
        "name_en": "Lavender",
        "emoji": "🌸",
        "family_he": "שפתניים",
        "cycle_days": 110,
        "notes_he": "אוהב pH יחסית בסיסי. גידול הידרופוני אתגרי — מתאים יותר לאדמה.",
        "ph_critical": [
            6.0,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.4,
                    6.8
                ],
                "ec": [
                    1000,
                    1400
                ],
                "ec_critical": 1800
            }
        ]
    },
    "stevia": {
        "name_he": "סטיביה",
        "name_en": "Stevia",
        "emoji": "🌿",
        "family_he": "מורכבים",
        "cycle_days": 80,
        "notes_he": "צמח מתוק טבעי. אוהב טמפ' חמה ושמש מלאה.",
        "ph_critical": [
            5.5,
            8.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.5
                ],
                "ec": [
                    1500,
                    2000
                ],
                "ec_critical": 2500
            }
        ]
    },
    "tomato": {
        "name_he": "עגבנייה",
        "name_en": "Tomato",
        "emoji": "🍅",
        "family_he": "סולניים",
        "cycle_days": 90,
        "notes_he": "צרכן הזנה גדול. EC עולה בפריחה לטעם מרוכז. דורש תמיכה מכנית.",
        "ph_critical": [
            5.0,
            6.8
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1000,
                    1200
                ],
                "ec_critical": 1500
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 21,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    1800,
                    2200
                ],
                "ec_critical": 2700
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 60,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    2500,
                    3500
                ],
                "ec_critical": 4200
            }
        ]
    },
    "cherry_tomato": {
        "name_he": "עגבניית שרי",
        "name_en": "Cherry Tomato",
        "emoji": "🍅",
        "family_he": "סולניים",
        "cycle_days": 75,
        "notes_he": "כמו עגבנייה רגילה אבל יותר עמיד. מהיר יותר לפרי.",
        "ph_critical": [
            5.0,
            6.8
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1000,
                    1200
                ],
                "ec_critical": 1500
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 18,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    1800,
                    2200
                ],
                "ec_critical": 2700
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 50,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    2400,
                    3200
                ],
                "ec_critical": 4000
            }
        ]
    },
    "pepper": {
        "name_he": "פלפל מתוק",
        "name_en": "Sweet Pepper",
        "emoji": "🌶️",
        "family_he": "סולניים",
        "cycle_days": 100,
        "notes_he": "EC נמוך יותר מעגבנייה. דורש pH יחסית חומצי.",
        "ph_critical": [
            5.2,
            6.5
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1000,
                    1200
                ],
                "ec_critical": 1500
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 21,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    1800,
                    2200
                ],
                "ec_critical": 2700
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 70,
                "ph": [
                    5.8,
                    6.3
                ],
                "ec": [
                    2000,
                    3000
                ],
                "ec_critical": 3600
            }
        ]
    },
    "hot_pepper": {
        "name_he": "פלפל חריף",
        "name_en": "Hot Pepper",
        "emoji": "🌶️",
        "family_he": "סולניים",
        "cycle_days": 95,
        "notes_he": "EC גבוה מגביר חריפות. עמיד יותר מפלפל מתוק.",
        "ph_critical": [
            5.0,
            6.8
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1000,
                    1200
                ],
                "ec_critical": 1500
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 21,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    2000,
                    2500
                ],
                "ec_critical": 3000
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 65,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    3000,
                    3500
                ],
                "ec_critical": 4000
            }
        ]
    },
    "eggplant": {
        "name_he": "חציל",
        "name_en": "Eggplant",
        "emoji": "🍆",
        "family_he": "סולניים",
        "cycle_days": 100,
        "notes_he": "צרכן הזנה כבד. אוהב טמפ' חמה ואור חזק.",
        "ph_critical": [
            5.0,
            7.0
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1000,
                    1400
                ],
                "ec_critical": 1700
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 21,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    2000,
                    2800
                ],
                "ec_critical": 3300
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 65,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    2500,
                    3500
                ],
                "ec_critical": 4200
            }
        ]
    },
    "cucumber": {
        "name_he": "מלפפון",
        "name_en": "Cucumber",
        "emoji": "🥒",
        "family_he": "דלועיים",
        "cycle_days": 60,
        "notes_he": "גידול מהיר. דורש תמיכת טיפוס וקצב צריכת מים גבוה.",
        "ph_critical": [
            5.2,
            6.5
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.0
                ],
                "ec": [
                    1000,
                    1400
                ],
                "ec_critical": 1700
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 14,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    1700,
                    2200
                ],
                "ec_critical": 2700
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 45,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    2200,
                    2500
                ],
                "ec_critical": 3000
            }
        ]
    },
    "zucchini": {
        "name_he": "קישוא",
        "name_en": "Zucchini",
        "emoji": "🥒",
        "family_he": "דלועיים",
        "cycle_days": 55,
        "notes_he": "צמח גדול שזקוק לרבה מקום. פרי מהיר מאוד אחרי פריחה.",
        "ph_critical": [
            5.5,
            6.8
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1000,
                    1400
                ],
                "ec_critical": 1700
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 14,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1600,
                    2000
                ],
                "ec_critical": 2500
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 40,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1800,
                    2400
                ],
                "ec_critical": 3000
            }
        ]
    },
    "yellow_squash": {
        "name_he": "קישוא צהוב",
        "name_en": "Yellow Squash",
        "emoji": "🥒",
        "family_he": "דלועיים",
        "cycle_days": 55,
        "notes_he": "זהה לקישוא בדרישות, פרי צהוב במקום ירוק.",
        "ph_critical": [
            5.5,
            6.8
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1000,
                    1400
                ],
                "ec_critical": 1700
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 14,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1600,
                    2000
                ],
                "ec_critical": 2500
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 40,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    1800,
                    2400
                ],
                "ec_critical": 3000
            }
        ]
    },
    "pumpkin": {
        "name_he": "דלעת",
        "name_en": "Pumpkin",
        "emoji": "🎃",
        "family_he": "דלועיים",
        "cycle_days": 110,
        "notes_he": "צמח ענק — לא מתאים למערכת קטנה. דורש תמיכה.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.5
                ],
                "ec": [
                    1000,
                    1400
                ],
                "ec_critical": 1700
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 21,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    1800,
                    2400
                ],
                "ec_critical": 2900
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 60,
                "ph": [
                    5.5,
                    6.5
                ],
                "ec": [
                    2000,
                    2400
                ],
                "ec_critical": 2900
            }
        ]
    },
    "melon": {
        "name_he": "מלון",
        "name_en": "Melon",
        "emoji": "🍈",
        "family_he": "דלועיים",
        "cycle_days": 90,
        "notes_he": "EC גבוה בפריחה מגביר מתיקות. רגיש לאדים.",
        "ph_critical": [
            5.5,
            6.8
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.3
                ],
                "ec": [
                    1000,
                    1400
                ],
                "ec_critical": 1700
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 18,
                "ph": [
                    5.8,
                    6.5
                ],
                "ec": [
                    1800,
                    2200
                ],
                "ec_critical": 2700
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 55,
                "ph": [
                    5.8,
                    6.5
                ],
                "ec": [
                    2000,
                    2500
                ],
                "ec_critical": 3000
            }
        ]
    },
    "watermelon": {
        "name_he": "אבטיח",
        "name_en": "Watermelon",
        "emoji": "🍉",
        "family_he": "דלועיים",
        "cycle_days": 100,
        "notes_he": "צמח ענק עם פרי גדול — דורש תמיכת רשת. מערכת חזקה דרושה.",
        "ph_critical": [
            5.5,
            6.8
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.5
                ],
                "ec": [
                    1000,
                    1400
                ],
                "ec_critical": 1700
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 18,
                "ph": [
                    5.8,
                    6.5
                ],
                "ec": [
                    1800,
                    2200
                ],
                "ec_critical": 2700
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 60,
                "ph": [
                    5.8,
                    6.5
                ],
                "ec": [
                    2000,
                    2500
                ],
                "ec_critical": 3000
            }
        ]
    },
    "broccoli": {
        "name_he": "ברוקולי",
        "name_en": "Broccoli",
        "emoji": "🥦",
        "family_he": "מצליבים",
        "cycle_days": 90,
        "notes_he": "צרכן הזנה כבד מאוד. צריך לעקוב אחרי סידן (Ca) למניעת blossom rot.",
        "ph_critical": [
            5.5,
            7.2
        ],
        "stages": [
            {
                "id": "vegfruit",
                "label_he": "צמיחה/פרי",
                "day_start": 0,
                "ph": [
                    6.0,
                    6.8
                ],
                "ec": [
                    2800,
                    3500
                ],
                "ec_critical": 4200
            }
        ]
    },
    "cauliflower": {
        "name_he": "כרובית",
        "name_en": "Cauliflower",
        "emoji": "🥦",
        "family_he": "מצליבים",
        "cycle_days": 100,
        "notes_he": "EC נמוך מברוקולי. רגיש לטמפ' — דורש קור.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "vegfruit",
                "label_he": "צמיחה/פרי",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    500,
                    2000
                ],
                "ec_critical": 2500
            }
        ]
    },
    "cabbage_white": {
        "name_he": "כרוב לבן",
        "name_en": "White Cabbage",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 110,
        "notes_he": "צרכן הזנה כבד. מחזור גידול ארוך, אבל יציב.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.5,
                    7.0
                ],
                "ec": [
                    2500,
                    3000
                ],
                "ec_critical": 3700
            }
        ]
    },
    "cabbage_red": {
        "name_he": "כרוב אדום",
        "name_en": "Red Cabbage",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 110,
        "notes_he": "זהה לכרוב לבן בדרישות, צבע סגול עז.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.5,
                    7.0
                ],
                "ec": [
                    2500,
                    3000
                ],
                "ec_critical": 3700
            }
        ]
    },
    "brussels_sprouts": {
        "name_he": "כרוב ניצנים",
        "name_en": "Brussels Sprouts",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 120,
        "notes_he": "מחזור ארוך מאוד. דורש קור לכרבולת איכותית.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.5,
                    7.5
                ],
                "ec": [
                    2500,
                    3000
                ],
                "ec_critical": 3700
            }
        ]
    },
    "kohlrabi": {
        "name_he": "קולרבי",
        "name_en": "Kohlrabi",
        "emoji": "🥬",
        "family_he": "מצליבים",
        "cycle_days": 60,
        "notes_he": "כרוב עם גזע נפוח. מחזור קצר יחסית למצליבים.",
        "ph_critical": [
            5.5,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.5,
                    7.0
                ],
                "ec": [
                    1800,
                    2400
                ],
                "ec_critical": 3000
            }
        ]
    },
    "radish": {
        "name_he": "צנון",
        "name_en": "Radish",
        "emoji": "🥕",
        "family_he": "מצליבים",
        "cycle_days": 30,
        "notes_he": "מחזור מהיר ביותר. EC נמוך יותר אחרי שהפקעת התחילה להתפתח.",
        "ph_critical": [
            5.5,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    1600,
                    2200
                ],
                "ec_critical": 2700
            }
        ]
    },
    "strawberry": {
        "name_he": "תות שדה",
        "name_en": "Strawberry",
        "emoji": "🍓",
        "family_he": "ורדיים",
        "cycle_days": 90,
        "notes_he": "דורש pH חומצי במיוחד למניעת נעילת ברזל. EC גבוה מדי פוגע בטעם.",
        "ph_critical": [
            5.0,
            6.2
        ],
        "stages": [
            {
                "id": "seedling",
                "label_he": "סטרטר",
                "day_start": 0,
                "ph": [
                    5.8,
                    6.2
                ],
                "ec": [
                    600,
                    1000
                ],
                "ec_critical": 1300
            },
            {
                "id": "vegetative",
                "label_he": "צמיחה",
                "day_start": 30,
                "ph": [
                    5.5,
                    6.0
                ],
                "ec": [
                    1200,
                    1400
                ],
                "ec_critical": 1700
            },
            {
                "id": "fruiting",
                "label_he": "פריחה/פרי",
                "day_start": 70,
                "ph": [
                    5.6,
                    6.0
                ],
                "ec": [
                    1400,
                    1600
                ],
                "ec_critical": 1900
            }
        ]
    },
    "peas": {
        "name_he": "אפונה",
        "name_en": "Peas",
        "emoji": "🫛",
        "family_he": "קטניות",
        "cycle_days": 65,
        "notes_he": "קטניה מקבעת חנקן. אל תיתן יותר מדי N.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    7.0
                ],
                "ec": [
                    800,
                    1800
                ],
                "ec_critical": 2200
            }
        ]
    },
    "beans": {
        "name_he": "שעועית",
        "name_en": "Beans",
        "emoji": "🫘",
        "family_he": "קטניות",
        "cycle_days": 60,
        "notes_he": "קטניה מקבעת חנקן. אוהבת חום, רגישה לקור.",
        "ph_critical": [
            5.5,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    6.5
                ],
                "ec": [
                    2000,
                    4000
                ],
                "ec_critical": 4500
            }
        ]
    },
    "garlic": {
        "name_he": "שום",
        "name_en": "Garlic",
        "emoji": "🧄",
        "family_he": "נרקיסיים",
        "cycle_days": 180,
        "notes_he": "מחזור גידול ארוך מאוד. הידרופוניקה אפשרית אבל לא טריוויאלית.",
        "ph_critical": [
            5.5,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    6.5
                ],
                "ec": [
                    1400,
                    1800
                ],
                "ec_critical": 2200
            }
        ]
    },
    "green_onion": {
        "name_he": "בצל ירוק",
        "name_en": "Green Onion",
        "emoji": "🧅",
        "family_he": "נרקיסיים",
        "cycle_days": 65,
        "notes_he": "קל לגידול. אפשר לקטוף עלים מספר פעמים מאותו צמח.",
        "ph_critical": [
            5.5,
            7.0
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.0,
                    6.7
                ],
                "ec": [
                    1400,
                    1800
                ],
                "ec_critical": 2200
            }
        ]
    },
    "leek": {
        "name_he": "כרישה",
        "name_en": "Leek",
        "emoji": "🧅",
        "family_he": "נרקיסיים",
        "cycle_days": 120,
        "notes_he": "צמח עמיד עם גזע עבה. דורש זמן אבל עמיד בקור.",
        "ph_critical": [
            5.5,
            7.5
        ],
        "stages": [
            {
                "id": "all",
                "label_he": "כל השלבים",
                "day_start": 0,
                "ph": [
                    6.5,
                    7.0
                ],
                "ec": [
                    1400,
                    1800
                ],
                "ec_critical": 2200
            }
        ]
    }
}


def get_plant_context(plant_id: str, stage_id: str = None) -> dict | None:
    """Return enriched context for a (plant_id, stage_id) pair.

    Returns a dict with: name_he, name_en, family_he, cycle_days, notes_he,
    ph_critical, ph_target (list[float] or None), ec_target (list[int] or None),
    ec_critical (int or None). Falls back gracefully if the stage is unknown:
    picks the first stage in the catalog so the LLM still gets *some* range.

    Returns None if the plant_id is not in the catalog.
    """
    plant = PLANTS.get(plant_id)
    if not plant:
        return None
    stages = plant.get('stages') or []
    stage = None
    if stage_id:
        for s in stages:
            if s.get('id') == stage_id:
                stage = s
                break
    if stage is None and stages:
        # Customer hasn't set a stage, or set one that no longer exists —
        # pick the widest "all" stage or the first defined stage.
        for s in stages:
            if s.get('id') == 'all':
                stage = s
                break
        if stage is None:
            stage = stages[0]
    return {
        'name_he': plant.get('name_he'),
        'name_en': plant.get('name_en'),
        'family_he': plant.get('family_he'),
        'cycle_days': plant.get('cycle_days'),
        'notes_he': plant.get('notes_he'),
        'ph_critical': plant.get('ph_critical'),
        'stage_id': stage.get('id') if stage else None,
        'stage_label_he': stage.get('label_he') if stage else None,
        'ph_target': stage.get('ph') if stage else None,
        'ec_target': stage.get('ec') if stage else None,
        'ec_critical': stage.get('ec_critical') if stage else None,
    }


def species_count() -> int:
    return len(PLANTS)
