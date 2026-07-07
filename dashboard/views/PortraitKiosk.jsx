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

const PortraitKiosk = () => {
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
  } = useDashboardData(mqtt, { weatherDefaults: { temperature: '—', condition: 'Clear', feels_like: '—' } });

  return (
    <div style={styles.container}>
      <div style={styles.glowTopLeft}></div>
      <div style={styles.glowBottomRight}></div>

      {/* HEADER SECTION */}
      <header style={styles.header}>
        <div style={styles.headerLeft}>
          <h1 style={styles.time}>{formatTime(currentTime, { showSeconds: true })}</h1>
          <p style={styles.dateText}>{formatDate(currentTime)}</p>
        </div>

        <div style={styles.weatherBlock}>
          <div style={styles.weatherMain}>
            <span style={styles.weatherIcon}>⚡</span>
            <h2 style={styles.tempText}>{weather.temperature}°C</h2>
          </div>
          <span style={styles.condText}>{weather.condition ? weather.condition.toUpperCase() : 'CLEAR'}</span>
        </div>
      </header>

      {/* FIXED SCREEN GRID SYSTEM */}
      <main className="responsive-layout-grid" style={styles.responsiveGrid}>

        {/* ROW 1: COMPACT HIERARCHICAL PRAYER TIME STRIP */}
        <section style={{ ...styles.glassCard, gridColumn: 'span 2', padding: '12px 16px' }}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #3b82f6, #06b6d4)' }}></div>
          <div style={styles.prayerHeader}>
            <span style={styles.prayerTitleIcon}>🕌</span>
            <div style={styles.prayerHeaderText}>
              <h2 style={styles.prayerMainTitle}>Prayer Times</h2>
              <span style={styles.prayerSubTitle}>• Today's Schedule</span>
            </div>
          </div>
          <div style={styles.prayerGrid}>
            {Object.entries(prayerTimes).map(([name, timeValue]) => (
              <div key={name} style={styles.prayerColumnCard}>
                <span style={styles.prayerCardIcon}>{PRAYER_ICONS[name] || '🕌'}</span>
                <span style={styles.prayerCardTime}>{timeValue}</span>
                <span style={styles.prayerCardName}>{name}</span>
              </div>
            ))}
          </div>
        </section>

        {/* ROW 2: FULL WIDTH HORIZONTAL MEAL OUTLOOK */}
        <section style={{ ...styles.glassCard, gridColumn: 'span 2' }}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #10b981, #3b82f6)' }}></div>
          <h2 style={styles.cardTitle}>🍽️ Menu Outlook</h2>
          <div style={styles.mealHorizontalRow}>
            <div style={styles.mealColumn}>
              <h3 style={styles.subTitle}>Today</h3>
              <div style={styles.mealItemRowContainer}>
                {getMealsForDay("today", todayDayName).map((meal, idx) => (
                  <div key={idx} style={styles.mealItem}>
                    <span style={styles.dotIndicator}>•</span>
                    <span style={styles.bodyTextMultiLine}>{tldrText(meal, 60)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div style={styles.mealColumn}>
              <h3 style={styles.subTitle}>Tomorrow</h3>
              <div style={styles.mealItemRowContainer}>
                {getMealsForDay("tomorrow", tomorrowDayName).map((meal, idx) => (
                  <div key={idx} style={styles.mealItem}>
                    <span style={styles.dotIndicator}>•</span>
                    <span style={styles.bodyTextMultiLine}>{tldrText(meal, 60)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ROW 3 - LEFT: FAMILY SCHEDULE */}
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
                    <span style={styles.bodyTextMultiLine}>{tldrText(apt.title, 80)}</span>
                  </div>
                );
              })
            ) : (
              <p style={styles.emptyText}>No upcoming events scheduled.</p>
            )}
          </div>
        </section>

        {/* ROW 3 - RIGHT: GROCERY LIST */}
        <section style={styles.glassCard}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #ec4899, #8b5cf6)' }}></div>
          <h2 style={styles.cardTitle}>🛒 Smart Grocery List</h2>
          <div style={styles.shoppingGrid}>
            {shoppingList.length > 0 ? (
              shoppingList.map((item, idx) => (
                <div key={idx} style={styles.shopItem}>
                  <div style={styles.checkboxRing}></div>
                  <span style={styles.bodyTextMultiLine}>{tldrText(item, 80)}</span>
                </div>
              ))
            ) : (
              <p style={styles.emptyText}>List cleared! Ready for next run.</p>
            )}
          </div>
        </section>

        {/* ROW 4 - LEFT: STICKY NOTES */}
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
                  <span style={styles.bodyTextMultiLine}>{tldrText(note.text, 140)}</span>
                </div>
              ))
            )}
          </div>
        </section>

        {/* ROW 4 - RIGHT: STATUS TRACKING */}
        <section style={styles.glassCard}>
          <div style={{ ...styles.cardAccentBar, background: 'linear-gradient(90deg, #06b6d4, #10b981)' }}></div>
          <h2 style={styles.cardTitle}>🏠 Status Tracking</h2>
          <div style={styles.presenceGrid}>
            {Object.entries(peopleHome).map(([name, status], idx) => {
              const isHome = status.toLowerCase() === 'home';
              return (
                <div key={idx} style={styles.presenceRow}>
                  <span style={styles.bodyTextMultiLine}>{name}</span>
                  <span style={{
                    ...styles.presenceStatus,
                    background: isHome ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                    color: isHome ? '#34d399' : '#f87171',
                    border: isHome ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid rgba(239, 68, 68, 0.3)',
                    flexShrink: 0
                  }}>
                    {status.toUpperCase()}
                  </span>
                </div>
              );
            })}
          </div>
        </section>

      </main>

      {/* CANVAS DISPLAY ENFORCEMENT */}
      <style>{`
        ::-webkit-scrollbar {
          display: none;
        }
        html, body {
          margin: 0;
          padding: 0;
          overflow: hidden !important;
          height: 100vh;
          background-color: #030712;
        }
        .responsive-layout-grid {
          display: grid;
          grid-template-columns: repeat(2, 1fr) !important;
          grid-template-rows: 0.55fr 1.1fr 1.4fr 1.4fr !important;
          gap: 16px;
          flex: 1;
          min-height: 0;
          overflow: hidden;
        }
      `}</style>

      {/* FOOTER STATUS */}
      <footer style={styles.footerSync}>
        <div style={{ ...styles.syncDot, background: connected ? '#10b981' : '#ef4444' }}></div>
        {connected ? 'LIVE DISPATCH LINK ACTIVE' : 'RECONNECTING HUB CONTROLLER...'}
      </footer>
    </div>
  );
};

const styles = {
  container: { padding: '20px', backgroundColor: '#030712', height: '100vh', color: '#ffffff', display: 'flex', flexDirection: 'column', boxSizing: 'border-box', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', position: 'relative', overflow: 'hidden' },
  glowTopLeft: { position: 'absolute', top: '-10%', left: '-10%', width: '40vw', height: '40vw', borderRadius: '50%', background: 'radial-gradient(circle, rgba(59,130,246,0.08) 0%, rgba(0,0,0,0) 70%)', pointerEvents: 'none' },
  glowBottomRight: { position: 'absolute', bottom: '-10%', right: '-10%', width: '45vw', height: '45vw', borderRadius: '50%', background: 'radial-gradient(circle, rgba(139,92,246,0.08) 0%, rgba(0,0,0,0) 70%)', pointerEvents: 'none' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', paddingBottom: '12px', borderBottom: '1px solid rgba(255, 255, 255, 0.05)', flexShrink: 0 },
  headerLeft: { display: 'flex', flexDirection: 'column' },
  time: { fontSize: '36px', fontWeight: '900', margin: 0, letterSpacing: '-1px', background: 'linear-gradient(180deg, #ffffff 0%, #9ca3af 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' },
  dateText: { color: '#9ca3af', fontSize: '14px', marginTop: '2px', fontWeight: '500', textTransform: 'uppercase', letterSpacing: '1px' },
  weatherBlock: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end' },
  weatherMain: { display: 'flex', alignItems: 'center', gap: '8px' },
  weatherIcon: { fontSize: '20px' },
  tempText: { fontSize: '28px', fontWeight: '800', margin: 0 },
  condText: { color: '#6b7280', fontSize: '10px', fontWeight: '700', letterSpacing: '1.5px', marginTop: '2px' },

  prayerHeader: { display: 'flex', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', marginBottom: '8px', gap: '6px', flexShrink: 0 },
  prayerTitleIcon: { fontSize: '16px', color: '#3b82f6' },
  prayerHeaderText: { display: 'flex', flexDirection: 'row', alignItems: 'center', gap: '4px' },
  prayerMainTitle: { fontSize: '13px', fontWeight: '700', color: '#ffffff', margin: 0 },
  prayerSubTitle: { fontSize: '11px', color: '#6b7280', margin: 0, fontWeight: '500' },
  prayerGrid: { display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '8px', flex: 1, minHeight: 0 },

  prayerColumnCard: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '4px 2px', background: 'rgba(255, 255, 255, 0.02)', border: '1px solid rgba(255, 255, 255, 0.05)', borderRadius: '10px', textAlign: 'center', height: '85%' },
  prayerCardIcon: { fontSize: '13px', marginBottom: '2px' },
  prayerCardTime: { fontSize: '13px', fontWeight: '800', color: '#ffffff', letterSpacing: '-0.3px' },
  prayerCardName: { fontSize: '10px', color: '#9ca3af', fontWeight: '700', marginTop: '1px', textTransform: 'capitalize' },

  responsiveGrid: { display: 'grid', gap: '16px', flex: 1, minHeight: 0 },
  glassCard: { position: 'relative', background: 'rgba(17, 24, 39, 0.45)', backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', borderRadius: '16px', padding: '16px', border: '1px solid rgba(255, 255, 255, 0.07)', boxShadow: '0 8px 32px 0 rgba(0, 0, 0, 0.37)', display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' },
  cardAccentBar: { position: 'absolute', top: 0, left: 0, right: 0, height: '3px' },
  cardTitle: { fontSize: '14px', fontWeight: '700', margin: '0 0 12px 0', color: '#f3f4f6', display: 'flex', alignItems: 'center', gap: '8px', letterSpacing: '-0.3px', flexShrink: 0 },
  subTitle: { fontSize: '11px', textTransform: 'uppercase', color: '#6b7280', letterSpacing: '1px', fontWeight: '700', marginBottom: '6px', flexShrink: 0 },

  mealHorizontalRow: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', flex: 1, minHeight: 0, overflow: 'hidden' },
  mealColumn: { display: 'flex', flexDirection: 'column', minHeight: 0 },
  mealItemRowContainer: { display: 'flex', flexDirection: 'column', gap: '6px', overflowY: 'auto', flex: 1, minHeight: 0 },

  listContainer: { display: 'flex', flexDirection: 'column', gap: '6px', overflowY: 'auto', flex: 1, minHeight: 0 },
  mealItem: { background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: '8px', border: '1px solid rgba(255, 255, 255, 0.02)', display: 'flex', alignItems: 'center', color: '#e5e7eb' },
  dotIndicator: { color: '#10b981', marginRight: '8px', fontSize: '16px', flexShrink: 0 },
  apptRow: { display: 'flex', flexDirection: 'column', gap: '4px', background: 'rgba(59, 130, 246, 0.04)', padding: '10px', borderRadius: '10px', border: '1px solid rgba(59, 130, 246, 0.1)' },
  apptBadgeBlock: { display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' },
  indexBadge: { background: 'rgba(239, 68, 68, 0.2)', color: '#ef4444', fontSize: '9px', fontWeight: '800', padding: '1px 4px', borderRadius: '3px' },
  dateBadge: { background: 'rgba(37, 99, 235, 0.2)', color: '#60a5fa', fontSize: '9px', fontWeight: '700', padding: '1px 4px', borderRadius: '3px' },
  timeBadge: { background: 'rgba(255, 255, 255, 0.06)', color: '#9ca3af', fontSize: '9px', padding: '1px 4px', borderRadius: '3px' },
  noteRow: { display: 'flex', flexDirection: 'column', gap: '4px', background: 'rgba(245, 158, 11, 0.04)', padding: '10px', borderRadius: '10px', border: '1px solid rgba(245, 158, 11, 0.1)' },
  noteMeta: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  noteIndex: { color: '#f59e0b', fontSize: '10px', fontWeight: '800' },
  noteTime: { color: '#6b7280', fontSize: '10px' },

  shoppingGrid: { display: 'flex', flexDirection: 'column', gap: '6px', overflowY: 'auto', flex: 1, minHeight: 0 },
  shopItem: { background: 'rgba(255, 255, 255, 0.03)', padding: '10px', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '10px', border: '1px solid rgba(255, 255, 255, 0.02)' },
  checkboxRing: { width: '14px', height: '14px', borderRadius: '50%', border: '2px solid rgba(255, 255, 255, 0.25)', flexShrink: 0 },
  presenceGrid: { display: 'flex', flexDirection: 'column', gap: '6px', overflowY: 'auto', flex: 1, minHeight: 0 },
  presenceRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255, 255, 255, 0.02)', padding: '8px 12px', borderRadius: '8px', gap: '10px' },
  presenceStatus: { fontSize: '9px', fontWeight: '800', padding: '2px 6px', borderRadius: '5px', letterSpacing: '0.5px' },

  // New Multiline styling block replacing single-line truncation locks
  bodyTextMultiLine: { fontSize: '13px', fontWeight: '500', color: '#e5e7eb', margin: 0, wordBreak: 'break-word', whiteSpace: 'pre-wrap', lineHeight: '1.4' },
  emptyText: { color: '#4b5563', fontSize: '12px', fontStyle: 'italic', margin: 0 },
  footerSync: { marginTop: '12px', fontSize: '10px', color: '#4b5563', display: 'flex', alignItems: 'center', gap: '6px', fontWeight: '600', letterSpacing: '0.5px', flexShrink: 0 },
  syncDot: { width: '6px', height: '6px', borderRadius: '50%' }
};

export default PortraitKiosk;
