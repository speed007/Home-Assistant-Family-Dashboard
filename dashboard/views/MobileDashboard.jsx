import React from 'react';
import mqtt from 'mqtt';
import {
  PRAYER_ICONS,
  tldrText,
  formatTime,
  formatDate,
  useClock,
  useDashboardData,
} from './dashboardShared';

const MobileDashboard = () => {
  const { currentTime, todayDayName, tomorrowDayName } = useClock();
  const {
    shoppingList,
    appointments,
    notes,
    weather,
    connected,
    peopleHome,
    prayerTimes,
    getMealsForDay,
  } = useDashboardData(mqtt);

  return (
    <div style={styles.container}>
      <div style={styles.glowTopLeft}></div>
      <div style={styles.glowBottomRight}></div>

      {/* HEADER BLOCK */}
      <header style={styles.header}>
        <div style={styles.headerLeft}>
          <h1 style={styles.time}>{formatTime(currentTime)}</h1>
          <p style={styles.dateText}>{formatDate(currentTime)}</p>
        </div>
        <div style={styles.weatherBlock}>
          <div style={styles.weatherMain}>
            <span style={styles.weatherIcon}>⚡</span>
            <h2 style={styles.tempText}>{weather.temperature}°C</h2>
          </div>
          <span style={styles.condText}>{(weather.condition || 'CLEAR').toUpperCase()}</span>
        </div>
      </header>

      {/* RESPONSIVE CARD GRID */}
      <main style={styles.responsiveGrid}>

        {/* PRAYER TIMES */}
        <section style={styles.glassCard}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #3b82f6, #06b6d4)' }}></div>
          <h2 style={styles.cardTitle}>🕌 Prayer Times</h2>
          <div style={styles.prayerMobileGrid}>
            {Object.entries(prayerTimes).map(([name, timeValue]) => (
              <div key={name} style={styles.prayerBadge}>
                <span style={styles.prayerIconText}>{PRAYER_ICONS[name]} {name}</span>
                <span style={styles.prayerTimeText}>{timeValue}</span>
              </div>
            ))}
          </div>
        </section>

        {/* MENU */}
        <section style={styles.glassCard}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #10b981, #3b82f6)' }}></div>
          <h2 style={styles.cardTitle}>🍽️ Menu Outlook</h2>
          <div style={styles.menuSplit}>
            <div style={{flex: 1}}>
              <h3 style={styles.subTitle}>Today</h3>
              {getMealsForDay("today", todayDayName).map((meal, idx) => (
                <div key={idx} style={styles.mealItem}>
                  <span style={styles.dotIndicator}>•</span>
                  <p style={styles.bodyText}>{tldrText(meal, 60)}</p>
                </div>
              ))}
            </div>
            <div style={{flex: 1}}>
              <h3 style={styles.subTitle}>Tomorrow</h3>
              {getMealsForDay("tomorrow", tomorrowDayName).map((meal, idx) => (
                <div key={idx} style={styles.mealItem}>
                  <span style={styles.dotIndicator}>•</span>
                  <p style={styles.bodyText}>{tldrText(meal, 60)}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* SCHEDULE */}
        <section style={styles.glassCard}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)' }}></div>
          <h2 style={styles.cardTitle}>📅 Family Schedule</h2>
          <div style={styles.listContainer}>
            {appointments.length > 0 ? (
              appointments.map((apt, idx) => {
                const aptDate = apt.date ? new Date(apt.date + 'T00:00:00').toLocaleDateString('en-GB', {month: 'short', day: 'numeric'}) : 'Plan';
                return (
                  <div key={idx} style={styles.apptRow}>
                    <div style={styles.apptBadgeBlock}>
                      {apt.index && <span style={styles.indexBadge}>#{apt.index}</span>}
                      <span style={styles.dateBadge}>{aptDate}</span>
                      <span style={styles.timeBadge}>{apt.time || 'All Day'}</span>
                    </div>
                    <p style={styles.bodyText}>{tldrText(apt.title, 80)}</p>
                  </div>
                );
              })
            ) : (
              <p style={styles.emptyText}>No upcoming events scheduled.</p>
            )}
          </div>
        </section>

        {/* GROCERIES */}
        <section style={styles.glassCard}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #ec4899, #8b5cf6)' }}></div>
          <h2 style={styles.cardTitle}>🛒 Smart Grocery List</h2>
          <div style={styles.listContainer}>
            {shoppingList.length > 0 ? (
              shoppingList.map((item, idx) => (
                <div key={idx} style={styles.shopItem}>
                  <div style={styles.checkboxRing}></div>
                  <p style={styles.bodyText}>{tldrText(item, 80)}</p>
                </div>
              ))
            ) : (
              <p style={styles.emptyText}>List cleared!</p>
            )}
          </div>
        </section>

        {/* STICKY NOTES */}
        <section style={styles.glassCard}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #f59e0b, #ef4444)' }}></div>
          <h2 style={styles.cardTitle}>📋 Active Sticky Notes</h2>
          <div style={styles.listContainer}>
            {notes.length === 0 ? (
              <p style={styles.emptyText}>No notes pinned right now.</p>
            ) : (
              notes.map((note, index) => (
                <div key={index} style={styles.noteRow}>
                  <div style={styles.noteMeta}>
                    <span style={styles.noteIndex}>#{note.index || (index + 1)}</span>
                    <span style={styles.noteTime}>{note.time || "Now"}</span>
                  </div>
                  <p style={styles.bodyText}>{tldrText(note.text, 140)}</p>
                </div>
              ))
            )}
          </div>
        </section>

        {/* STATUS TRACKING */}
        <section style={styles.glassCard}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #06b6d4, #10b981)' }}></div>
          <h2 style={styles.cardTitle}>🏠 Status Tracking</h2>
          <div style={styles.listContainer}>
            {Object.entries(peopleHome).map(([name, status], idx) => {
              const isHome = status.toLowerCase() === 'home';
              return (
                <div key={idx} style={styles.presenceRow}>
                  <p style={styles.bodyText}>{name}</p>
                  <span style={{
                    ...styles.presenceStatus,
                    background: isHome ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                    color: isHome ? '#34d399' : '#f87171',
                    border: isHome ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid rgba(239, 68, 68, 0.3)',
                  }}>{status.toUpperCase()}</span>
                </div>
              );
            })}
          </div>
        </section>

      </main>

      {/* SYNC FOOTER */}
      <footer style={styles.footerSync}>
        <div style={{ ...styles.syncDot, background: connected ? '#10b981' : '#ef4444' }}></div>
        {connected ? 'LIVE DISPATCH LINK ACTIVE' : 'RECONNECTING HUB CONTROLLER...'}
      </footer>
    </div>
  );
};

const styles = {
  container: { padding: '16px', backgroundColor: '#030712', minHeight: '100vh', color: '#ffffff', display: 'flex', flexDirection: 'column', boxSizing: 'border-box', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', position: 'relative', overflowX: 'hidden' },
  glowTopLeft: { position: 'absolute', top: '-10%', left: '-10%', width: '60vw', height: '60vw', borderRadius: '50%', background: 'radial-gradient(circle, rgba(59,130,246,0.05) 0%, rgba(0,0,0,0) 70%)', pointerEvents: 'none' },
  glowBottomRight: { position: 'absolute', bottom: '-10%', right: '-10%', width: '60vw', height: '60vw', borderRadius: '50%', background: 'radial-gradient(circle, rgba(139,92,246,0.05) 0%, rgba(0,0,0,0) 70%)', pointerEvents: 'none' },

  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', paddingBottom: '12px', borderBottom: '1px solid rgba(255, 255, 255, 0.05)', flexShrink: 0 },
  headerLeft: { display: 'flex', flexDirection: 'column' },
  time: { fontSize: '28px', fontWeight: '900', margin: 0, background: 'linear-gradient(180deg, #ffffff 0%, #9ca3af 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' },
  dateText: { color: '#9ca3af', fontSize: '12px', marginTop: '2px', fontWeight: '500', textTransform: 'uppercase', letterSpacing: '0.5px' },
  weatherBlock: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end' },
  weatherMain: { display: 'flex', alignItems: 'center', gap: '6px' },
  weatherIcon: { fontSize: '16px' },
  tempText: { fontSize: '22px', fontWeight: '800', margin: 0 },
  condText: { color: '#6b7280', fontSize: '9px', fontWeight: '700', letterSpacing: '1px', marginTop: '2px' },

  responsiveGrid: { display: 'flex', flexDirection: 'row', flexWrap: 'wrap', gap: '16px', width: '100%' },
  glassCard: { position: 'relative', background: 'rgba(17, 24, 39, 0.45)', backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', borderRadius: '16px', padding: '16px', border: '1px solid rgba(255, 255, 255, 0.07)', boxShadow: '0 8px 32px 0 rgba(0, 0, 0, 0.37)', display: 'flex', flexDirection: 'column', flex: '1 1 320px', boxSizing: 'border-box', overflow: 'hidden' },
  cardAccentBar: { position: 'absolute', top: 0, left: 0, right: 0, height: '3px' },
  cardTitle: { fontSize: '13px', fontWeight: '700', margin: '0 0 12px 0', color: '#f3f4f6', textTransform: 'uppercase', letterSpacing: '0.5px' },
  subTitle: { fontSize: '11px', textTransform: 'uppercase', color: '#6b7280', letterSpacing: '0.5px', fontWeight: '700', marginBottom: '8px' },

  prayerMobileGrid: { display: 'flex', flexDirection: 'column', gap: '8px' },
  prayerBadge: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 12px', background: 'rgba(255, 255, 255, 0.02)', border: '1px solid rgba(255, 255, 255, 0.04)', borderRadius: '10px' },
  prayerIconText: { fontSize: '13px', fontWeight: '600', color: '#9ca3af', textTransform: 'capitalize' },
  prayerTimeText: { fontSize: '13px', fontWeight: '800', color: '#ffffff' },

  menuSplit: { display: 'flex', flexDirection: 'column', gap: '14px' },
  mealItem: { background: 'rgba(255, 255, 255, 0.02)', padding: '8px 12px', borderRadius: '8px', display: 'flex', alignItems: 'center', border: '1px solid rgba(255, 255, 255, 0.01)' },
  dotIndicator: { color: '#10b981', marginRight: '8px', fontSize: '14px' },

  listContainer: { display: 'flex', flexDirection: 'column', gap: '8px' },
  apptRow: { display: 'flex', flexDirection: 'column', gap: '6px', background: 'rgba(59, 130, 246, 0.03)', padding: '10px', borderRadius: '10px', border: '1px solid rgba(59, 130, 246, 0.08)' },
  apptBadgeBlock: { display: 'flex', gap: '6px', alignItems: 'center' },
  indexBadge: { background: 'rgba(239, 68, 68, 0.15)', color: '#ef4444', fontSize: '9px', fontWeight: '800', padding: '2px 5px', borderRadius: '4px' },
  dateBadge: { background: 'rgba(37, 99, 235, 0.15)', color: '#60a5fa', fontSize: '9px', fontWeight: '700', padding: '2px 5px', borderRadius: '4px' },
  timeBadge: { background: 'rgba(255, 255, 255, 0.05)', color: '#9ca3af', fontSize: '9px', padding: '2px 5px', borderRadius: '4px' },

  shopItem: { background: 'rgba(255, 255, 255, 0.02)', padding: '10px 12px', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '10px', border: '1px solid rgba(255, 255, 255, 0.01)' },
  checkboxRing: { width: '12px', height: '12px', borderRadius: '50%', border: '2px solid rgba(255, 255, 255, 0.2)', flexShrink: 0 },

  noteRow: { display: 'flex', flexDirection: 'column', gap: '6px', background: 'rgba(245, 158, 11, 0.03)', padding: '10px', borderRadius: '10px', border: '1px solid rgba(245, 158, 11, 0.08)' },
  noteMeta: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  noteIndex: { color: '#f59e0b', fontSize: '10px', fontWeight: '800' },
  noteTime: { color: '#6b7280', fontSize: '10px' },

  presenceRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255, 255, 255, 0.02)', padding: '8px 12px', borderRadius: '8px' },
  presenceStatus: { fontSize: '9px', fontWeight: '800', padding: '2px 6px', borderRadius: '5px', letterSpacing: '0.3px' },

  bodyText: { fontSize: '13px', fontWeight: '500', color: '#e5e7eb', margin: 0, wordBreak: 'break-word', whiteSpace: 'pre-wrap', lineHeight: '1.4' },
  emptyText: { color: '#4b5563', fontSize: '12px', fontStyle: 'italic', margin: 0 },
  footerSync: { marginTop: '24px', fontSize: '9px', color: '#4b5563', display: 'flex', alignItems: 'center', gap: '6px', fontWeight: '600', letterSpacing: '0.5px' },
  syncDot: { width: '6px', height: '6px', borderRadius: '50%' }
};

export default MobileDashboard;
