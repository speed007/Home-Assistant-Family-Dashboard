import { useState, useEffect } from 'react';

// ---- Shared static data ----

export const WEEKLY_MEAL_PLAN = {
  monday: ["Moong Daal - Mugdhon", "Yellow moong Daal", "Kitchdi - Ringru/KARI-Potatoe", "Khatta binda", "KIDNEY BEANS", "SARAGWO"],
  tuesday: ["Chicken Curry", "Chicken Tikka", "Steamed Chicken", "Grilled Chicken with Mash", "BUTTER CHICKEN"],
  wednesday: ["Chicken pie", "Pasta", "Sheppards pie", "Jacket potatoe", "LASAGNE"],
  thursday: ["Fish Curry", "Grilled Fish", "Steamed Fish", "Home made Fish & Chips", "SPINACH + PANEER"],
  friday: ["Daal Chawal", "Biryani", "Yakni", "Nihaari - Daleem", "Chinese Palau"],
  saturday: ["Chinese", "Pizza", "Take out", "Sausages + mash"],
  sunday: ["Chip - burger @ Home", "Noodles", "Kebab roll", "Take out"]
};

export const PRAYER_ICONS = { Fajr: '🌅', Dhuhr: '☀️', Asr: '🌤️', Maghrib: '🌇', Isha: '🌙' };

export const DEFAULT_PEOPLE_HOME = { 'Father': 'Home', 'Mother': 'Work', 'Kids': 'School' };

export const DEFAULT_PRAYER_TIMES = {
  Fajr: '02:36 AM',
  Dhuhr: '01:11 PM',
  Asr: '06:51 PM',
  Maghrib: '09:32 PM',
  Isha: '10:37 PM'
};

const MQTT_TOPICS = [
  'home/dashboard/shopping_list',
  'home/dashboard/calendar_events',
  'home/dashboard/manual_appointments',
  'home/dashboard/meal_plan',
  'home/dashboard/daily_notes',
  'home/dashboard/weather',
  'home/dashboard/presence',
  'home/dashboard/prayer_times'
];

// Generous maximum length boundary fallback to preserve screen layout structure
export const tldrText = (text, maxLength = 120) => {
  if (!text) return '';
  return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
};

export const formatTime = (date, { showSeconds = false } = {}) =>
  new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    ...(showSeconds ? { second: '2-digit' } : {}),
    hour12: true,
  }).format(date);

export const formatDate = (date) =>
  new Intl.DateTimeFormat('en-GB', { weekday: 'long', month: 'short', day: 'numeric' }).format(date);

// ---- Clock ----
// Ticks once a second and derives today/tomorrow's day names for meal lookups.
export function useClock() {
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const todayDayName = currentTime.toLocaleDateString('en-GB', { weekday: 'long' }).toLowerCase();
  const tomorrowDayName = new Date(currentTime.getTime() + 86400000)
    .toLocaleDateString('en-GB', { weekday: 'long' })
    .toLowerCase();

  return { currentTime, todayDayName, tomorrowDayName };
}

// ---- MQTT-backed dashboard data ----
// Connects once, subscribes to every dashboard topic, and exposes derived state.
// `mqtt` is passed in (rather than imported here) so both views share one connection setup.
export function useDashboardData(mqtt, { weatherDefaults } = {}) {
  const [shoppingList, setShoppingList] = useState([]);
  const [haAppointments, setHaAppointments] = useState([]);
  const [manualAppointments, setManualAppointments] = useState([]);
  const [meals, setMeals] = useState({});
  const [notes, setNotes] = useState([]);
  const [weather, setWeather] = useState(weatherDefaults || { temperature: '—', condition: 'Clear' });
  const [connected, setConnected] = useState(false);
  const [peopleHome, setPeopleHome] = useState(DEFAULT_PEOPLE_HOME);
  const [prayerTimes, setPrayerTimes] = useState(DEFAULT_PRAYER_TIMES);

  const MQTT_BROKER = import.meta.env.VITE_MQTT_BROKER_WS;
  const MQTT_USER = import.meta.env.VITE_MQTT_USER;
  const MQTT_PASS = import.meta.env.VITE_MQTT_PASS;

  useEffect(() => {
    if (!MQTT_BROKER) {
      // Fails loudly instead of silently trying to connect to `undefined` —
      // usually means .env is missing or wasn't picked up at build time.
      console.error('Missing VITE_MQTT_BROKER_WS — check your .env file (see .env.example)');
      return;
    }

    const client = mqtt.connect(MQTT_BROKER, {
      username: MQTT_USER,
      password: MQTT_PASS,
      clean: true,
      reconnectPeriod: 1000,
    });

    client.on('connect', () => {
      setConnected(true);
      client.subscribe(MQTT_TOPICS);
    });

    client.on('message', (topic, message) => {
      try {
        const data = JSON.parse(message.toString());
        if (topic === 'home/dashboard/shopping_list') {
          if (data.items) setShoppingList(data.items);
        } else if (topic === 'home/dashboard/calendar_events') {
          setHaAppointments(Array.isArray(data) ? data : data.events || []);
        } else if (topic === 'home/dashboard/manual_appointments') {
          if (data.events) setManualAppointments(data.events);
        } else if (topic === 'home/dashboard/meal_plan') {
          setMeals(data.meals || {});
        } else if (topic === 'home/dashboard/daily_notes') {
          if (data.notes) setNotes(data.notes);
        } else if (topic === 'home/dashboard/weather') {
          setWeather(data);
        } else if (topic === 'home/dashboard/presence') {
          setPeopleHome(data);
        } else if (topic === 'home/dashboard/prayer_times') {
          setPrayerTimes(data);
        }
      } catch (err) {
        console.error('Parse error:', err);
      }
    });

    client.on('disconnect', () => setConnected(false));
    return () => client.end();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const appointments = [...haAppointments, ...manualAppointments].sort((a, b) =>
    (a.date || '9999-99-99').localeCompare(b.date || '9999-99-99')
  );

  const getMealsForDay = (dayKey, dayName) => {
    if (meals && meals[dayKey]) {
      return Array.isArray(meals[dayKey]) ? meals[dayKey] : [meals[dayKey]];
    }
    return WEEKLY_MEAL_PLAN[dayName] || ["No options set"];
  };

  return {
    shoppingList,
    appointments,
    meals,
    notes,
    weather,
    connected,
    peopleHome,
    prayerTimes,
    getMealsForDay,
  };
}
