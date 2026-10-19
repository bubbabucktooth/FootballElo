"""Shared table presentation for game history and statistics."""
from html import escape
import pandas as pd
import streamlit as st


def render_table(frame, key, selected=None, formats=None):
    """Use the league table's presentation with explicit sorting and CSV export."""
    formats = formats or {}
    frame = frame.copy()
    for name in frame.columns:
        if frame[name].dtype == object:
            frame[name] = frame[name].map(lambda value: str(value) if isinstance(value, (dict, list, tuple)) else value)
    sort_column, sort_direction = st.columns([2, 1])
    with sort_column:
        column = st.selectbox('Sortera efter', list(frame.columns), key=f'{key}_sort')
    with sort_direction:
        direction = st.segmented_control('Ordning', ['Stigande', 'Fallande'], default='Stigande',
                                         required=True, key=f'{key}_direction')
    descending = direction == 'Fallande'
    with st.expander('Exportera tabellen'):
        st.download_button('Hämta CSV', frame.to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'{key}.csv', mime='text/csv', key=f'{key}_csv')
    ordered = frame.sort_values(column, ascending=not descending, na_position='last', kind='stable')
    headers = ''.join(f'<th scope="col">{escape(str(c))}</th>' for c in frame.columns)
    rows = []
    for record in ordered.to_dict('records'):
        cells = []
        for name, value in record.items():
            text = '–' if pd.isna(value) else formats[name].format(value) if name in formats else str(value)
            cells.append(f'<td>{escape(text)}</td>')
        own = 'own-team' if record.get('Lag') == selected else ''
        rows.append(f'<tr class="{own}">' + ''.join(cells) + '</tr>')
    st.markdown('<div class="league-wrap statistics-table-wrap"><table class="league-table statistics-table">'
                '<thead><tr>' + headers + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>',
                unsafe_allow_html=True)
