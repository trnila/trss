#!/usr/bin/env python3

import curses
import re
import sys

import feedparser
import requests
import json
import os

APP_DIR = os.path.expanduser("~/.config/trss/")

def pipe_github(item):
    item['summary'] += "\n" + requests.get(item['link'] + ".patch").content.decode('utf-8')


class Feeds:
    def __init__(self, bus):
        self.sources = self.load_urls()
        self.bus = bus
        self.items = []
        self.storage_path = os.path.join(APP_DIR, "db.json")

    def load_urls(self):
        with open(os.path.join(APP_DIR, "urls")) as f:
            return json.load(f)

    def load(self):
        try:
            with open(self.storage_path) as f:
                self.items = json.load(f)
            self.bus.emit(Bus.ITEMS_LOADED, self.items)
        except FileNotFoundError as e:
            pass

    def save(self):
        with open(self.storage_path, 'w') as f:
            json.dump(self.items, f, indent=4)

    def refresh(self):
        for title, url in self.sources.items():
            self.parse_feed(title, url)
        self.save()
        self.bus.emit(Bus.ITEMS_LOADED, self.items)

    def find_one(self, **kwargs):
        for item in self.items:
            accept = True
            for k, v in kwargs.items():
                if item[k] != v:
                    accept = False
                    break

            if accept:
                return item


    def parse_feed(self, title, url):
        feed = feedparser.parse(url)
        for item in feed['entries']:
            our = self.find_one(link=item['link'])

            if not our:
                item['read'] = False
                item['source'] = title
                #pipe_github(item)
                self.items.append(item)


    def mark_read(self, link, read=True):
        for item in self.items:
            if item['link'] == link:
                item['read'] = read
        self.bus.emit(Bus.ITEM_READ, link)

class Bus:
    ITEMS_LOADED = 'items_loaded'
    ITEM_ACTIVATE = 'item_activated'
    ITEM_READ = 'item_read'

    def __init__(self):
        self.events = {}

    def register(self, name, fn):
        if name not in self.events:
            self.events[name] = []
        self.events[name].append(fn)

    def emit(self, name, *wargs):
        for fn in self.events.get(name, []):
            fn(*wargs)

class AttrText:
    def __init__(self, num, s, normal=None, highlight=None, is_category=False):
        self.num = num
        self.text = s
        self.normal = normal
        self.highlight = highlight
        self.is_category = True

class List:
    def __init__(self, cols, bus):
        bus.register(Bus.ITEMS_LOADED, self.on_new_items)
        bus.register(Bus.ITEM_READ, self.on_item_read)
        self.bus = bus

        self.height, self.width = (curses.LINES - 2, cols - 1)

        self.pad = curses.newpad(32767, self.width)
        self.pad_y = 0
        self.selected = 0
        self.items = []
        self.source_items = []
        self.query = {'read': False}

    def filter_by(self, **query):
        filtered = []
        for item in self.source_items:
            accept = True
            for k, v in query.items():
                if item[k] != v:
                    accept = False
                    break

            if accept:
                filtered.append(item)
        return filtered

    def filter(self):
        self.items = sorted(self.filter_by(**self.query), key=lambda i: (i['source'], i['updated']), reverse=True)

    def selected_item(self):
        if not self.items:
            return None

        label = self.line_to_item[self.selected]
        if label.num < 0:
            return None
        return self.items[label.num]


    def on_new_items(self, items):
        self.source_items = items
        self.selected = 0
        self.pad_y = 0

        self.filter()
        self.render_again()

    def render_again(self):
        self.pad.clear()

        items = []
        last_source = None
        for i in range(len(self.items)):
            if last_source != self.items[i]['source']:
                last_source = self.items[i]['source']
                items.append(
                    AttrText(
                        -1,
                        "{} {}".format(last_source, len(self.filter_by(source=last_source))),
                        curses.color_pair(3),
                        curses.color_pair(2)
                    )
                )
            items.append(
                AttrText(
                    i,
                    self.format_item(self.items[i]),
                    curses.color_pair(1),
                    curses.color_pair(2)
                )
            )

        self.line_to_item = items
        for i, item in enumerate(items):
          self.render_item(i)

        self.bus.emit(Bus.ITEM_ACTIVATE, self.selected_item())
        self.refresh()

    def render_item(self, i, highlight=False):
        item = self.line_to_item[i]
        color = item.highlight if highlight else item.normal
        
        if not self.items[item.num]['read'] and item.num >= 0:
            color |= curses.A_BOLD

        self.pad.addstr(i, 0, item.text, color)

    def on_item_read(self, url):
        self.render_again()

    def format_item(self, item):
        return f"{item['title']}\n"[0:self.width]

    def focus_next(self, n=1):
      self.render_item(self.selected)
      self.selected = min(len(self.line_to_item) - 1, self.selected + n)

      if n > 1:
          self.pad_y = self.selected
      if self.selected >= self.pad_y + curses.LINES:
          self.pad_y += 1
      self.render_item(self.selected, True)

      self.bus.emit(Bus.ITEM_ACTIVATE, self.selected_item())

    def focus_prev(self, n=1):
      self.render_item(self.selected)
      self.selected = max(0, self.selected - n)
      
      if n > 1:
          self.pad_y = self.selected
      elif self.selected + 1 == self.pad_y:
          self.pad_y -= 1
      self.render_item(self.selected, True)

      self.bus.emit(Bus.ITEM_ACTIVATE, self.selected_item())

    def refresh(self):
        self.pad.refresh(self.pad_y, 0, 0, 0, self.height, self.width)

    def handle(self, ch):
      if ch == curses.KEY_DOWN:
          self.focus_next()
      elif ch == curses.KEY_UP:
          self.focus_prev()
      elif ch == curses.KEY_NPAGE:
          self.focus_next(self.height)
      elif ch == curses.KEY_PPAGE:
          self.focus_prev(self.height)
      elif chr(ch) == 'a':
          if 'read' in self.query:
              del(self.query['read'])
          else:
              self.query['read'] = False
          self.filter()
          self.render_again()

class Detail:
    def __init__(self, offset, bus):
        bus.register(Bus.ITEM_ACTIVATE, self.show_detail)

        self.win = curses.newpad(32000, 100)
        self.height = curses.LINES - 2
        self.offset = offset
        self.content = ""
        self.y = 0

    def show_detail(self, item):
        self.y = 0
        self.content = html_to_text(item['summary']) if item else ""
        self.refresh()

    def refresh(self):
        self.win.clear()
        self.win.addstr(0, 0, self.content)
        self.win.refresh(self.y, 0, 0, self.offset, self.height, curses.COLS - 1 - self.offset)

    def handle(self, ch):
        lines = self.content.count('\n')
        if ch == curses.KEY_DOWN and self.y + 1 + curses.LINES <= lines:
            self.y += 1
        elif ch == curses.KEY_UP and self.y > 0:
            self.y -= 1
        elif ch == curses.KEY_PPAGE:
            self.y = max(0, self.y - self.height)
        elif ch == curses.KEY_NPAGE:
            self.y = min(lines - 1, self.y + self.height)

class Status:
    def __init__(self):
        self.win = curses.newwin(1, curses.COLS, curses.LINES - 1, 0)
        self.focus = 0
        self.info = ""

    def refresh(self):
        line = f"{'>' if self.focus else '<'} {self.info}"
        self.win.addstr(0, 0, line)
        self.win.refresh()


    def handle(self, ch):
        pass

def html_to_text(s):
    return re.sub(r'</?[^>]+>', '', s)

bus = Bus()
feeds = Feeds(bus)


def main(scr):
  scr.keypad(True)
  curses.use_default_colors()
  curses.noecho()
  curses.curs_set(False)
  scr.refresh()

  curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
  curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)
  curses.init_pair(3, curses.COLOR_BLUE, curses.COLOR_BLACK)

  list_cols = 40
  l = List(list_cols, bus)

  d = Detail(list_cols, bus)

  s = Status()

  widgets = [l, d, s]

  feeds.load()

  while True:
      ch = scr.getch()
      if ch < 256 and chr(ch) == 'q':
          break

      if ch == curses.KEY_RIGHT:
          s.focus = 1
      elif ch == curses.KEY_LEFT:
          s.focus = 0
      elif chr(ch) == 'n':
          item = l.selected_item()
          if item:
              feeds.mark_read(item['link'], not item['read'])
              if item['read']:
                  l.focus_next()
              feeds.save()
      elif chr(ch) == 'r':
          s.info = "Downloading..."
          s.refresh()
          feeds.refresh()
          s.info = ""

      if s.focus == 0:
          l.handle(ch)
      else:
        d.handle(ch)

      for w in widgets:
          w.refresh()


def wrap(fn):
    try:
        win = curses.initscr()
        curses.noecho()
        curses.cbreak()
        win.keypad(1)
        curses.start_color()
        fn(win)
    except Exception as e:
        win.keypad(0)
        curses.endwin()
        extype, value, tb = sys.exc_info()
        import traceback, pdb
        traceback.print_exc()
        pdb.post_mortem(tb)
    finally:
        win.keypad(0)
        curses.endwin()


wrap(main)
#curses.wrapper(main)
