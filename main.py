import asyncio
import json
import os
import sys
from datetime import datetime
from abc import ABC

import ccxt.pro as ccxt

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QTextEdit, QSplitter, QGroupBox, QHeaderView, QMessageBox, QInputDialog,
    QStatusBar, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
import qasync
from qasync import asyncSlot

def is_valid_proxy(proxy):
    if not proxy:
        return False
    for key in ("host", "port", "username", "password"):
        value = proxy.get(key, "")
        if not value or value.strip() == "***":
            return False
    return True

class BaseExchange(ABC):
    def __init__(self, exchange_class, keys, proxy=None):
        self.api_key = keys.get("apiKey")
        self.secret = keys.get("secret")
        self.password = keys.get("password")
        self.uid = keys.get("uid")
        self.exchange_class = exchange_class
        self.exchange = None
        self.proxy = proxy if is_valid_proxy(proxy) else None
        self.exchange_id = self.exchange_class.__name__

    async def __aenter__(self):
        proxy_url = get_proxy_url(self.proxy) if self.proxy else None
        options = {
            'apiKey': self.api_key,
            'secret': self.secret,
            'password': self.password,
            'uid': self.uid
        }
        
        if proxy_url:
            options['socksProxy'] = proxy_url

        self.exchange = self.exchange_class(options)
        await self.exchange.load_markets()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.exchange.close()


def get_proxy_url(proxy):
    return f"http://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"


async def sell_token(exchange, symbol, amount, price):
    price_str = f"{price:.10f}".rstrip('0').rstrip('.')
    order = await exchange.create_limit_sell_order(symbol, amount, price_str)
    return order


async def sell_in_parts(exchange, symbol, total_amount, price):
    markets = exchange.markets
    market = markets[symbol]
    limits = market.get('limits', {})
    max_amount = limits.get('amount', {}).get('max')

    if not max_amount:
        order = await sell_token(exchange, symbol, total_amount, price)
        return [order]

    orders = []
    remaining = total_amount

    while remaining > 0:
        amount_to_sell = min(remaining, max_amount)
        price_str = f"{price:.10f}".rstrip('0').rstrip('.')
        order = await exchange.create_limit_sell_order(symbol, amount_to_sell, price_str)
        orders.append(order)
        remaining -= amount_to_sell

    return orders


async def fetch_order(exchange, order_id, symbol):
    try:
        order = await exchange.fetch_order(order_id, symbol)
        return order
    except:
        return None


class TaskManager(QObject):
    task_updated = pyqtSignal(str, dict)
    log_message = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.tasks = {}
        self.loaded_markets = {}
        
    async def fetch_balance_and_sell_loop(self, task_id, exchange_key, exchange_class, keys, proxy, symbol):
        symbol = symbol.upper()
        current_exchange = None
        task_data = {
            'exchange': exchange_key,
            'symbol': symbol,
            'price': '0',
            'in_order': 0,
            'status': 'Инициализация'
        }
        
        while True:
            try:
                if current_exchange is None:
                    async with BaseExchange(exchange_class, keys, proxy) as exc:
                        current_exchange = exc.exchange

                balance = await current_exchange.fetch_balance()
                token = symbol.split('/')[0]

                ticker = await current_exchange.fetch_ticker(symbol)
                last_price = ticker.get('last', 0)

                token_balance = balance['free'].get(token, 0)
                equivalent_in_usdt = token_balance * last_price

                sell_price = self.tasks[task_id]["price"]
                order_id = self.tasks[task_id].get("order_id")

                task_data.update({
                    'exchange': exchange_key,
                    'symbol': symbol,
                    'price': f"{sell_price:.10f}".rstrip('0').rstrip('.'),
                    'in_order': 0,
                    'status': 'Работает'
                })

                if order_id:
                    order = await fetch_order(current_exchange, order_id, symbol)
                    if order:
                        remaining = order.get('remaining', 0)
                        if remaining > 0:
                            task_data['in_order'] = remaining
                            task_data['status'] = "Выполняется"
                        else:
                            self.tasks[task_id]["order_id"] = None
                            task_data['status'] = "Исполнен"
                    else:
                        if equivalent_in_usdt < 1:
                            self.tasks[task_id]["order_id"] = None
                            task_data['status'] = "Исполнен"
                        else:
                            task_data['status'] = "Нет ордера"
                else:
                    if equivalent_in_usdt > 1:
                        amount_to_sell = token_balance
                        orders = await sell_in_parts(current_exchange, symbol, amount_to_sell, sell_price)

                        if orders:
                            first_order = orders[0]
                            order_id = first_order.get('id', 'None')
                            self.tasks[task_id]["order_id"] = order_id
                            order_amount = sum(o.get('amount', 0) for o in orders)
                            task_data['in_order'] = order_amount
                            task_data['status'] = "Ордер создан"
                            
                            self.log_message.emit(
                                f"[{exchange_key}] Ордер создан: {symbol}, "
                                f"Количество={order_amount}, Цена={sell_price}"
                            )
                        else:
                            task_data['status'] = "Ошибка создания ордера"
                    else:
                        task_data['status'] = "Недостаточно средств"

                self.task_updated.emit(task_id, task_data)
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                task_data['status'] = "Отменено"
                self.task_updated.emit(task_id, task_data)
                return
            except Exception as e:
                self.log_message.emit(f"[{exchange_key}] Ошибка: {e}")
                task_data['status'] = "Ошибка, переподключение..."
                self.task_updated.emit(task_id, task_data)
                current_exchange = None
                await asyncio.sleep(10)

    async def load_exchange_markets(self, exchange_key, exchange_class, keys, proxy):
        try:
            if exchange_key not in self.loaded_markets:
                async with BaseExchange(exchange_class, keys, proxy) as current_exchange:
                    markets = current_exchange.exchange.markets
                    self.loaded_markets[exchange_key] = markets
                    self.log_message.emit(f"Загружено {len(markets)} символов для {exchange_key}")
            return list(self.loaded_markets[exchange_key].keys())
        except Exception as e:
            self.log_message.emit(f"Ошибка загрузки markets для {exchange_key}: {e}")
            return []

    async def get_current_price(self, exchange_key, exchange_class, keys, proxy, symbol):
        symbol = symbol.upper()
        try:
            async with BaseExchange(exchange_class, keys, proxy) as current_exchange:
                ticker = await current_exchange.exchange.fetch_ticker(symbol)
                return ticker.get('last')
        except Exception as e:
            self.log_message.emit(f"Ошибка получения цены для {symbol}: {e}")
            return None


class SellerMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.task_manager = TaskManager()
        self.setup_ui()
        self.setup_connections()
        self.load_config()
        self.setup_dark_theme()

    def setup_ui(self):
        self.setWindowTitle("Продажа токенов - PyQt6")
        self.setGeometry(100, 100, 1400, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)

        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        self.trading_tab = self.create_trading_tab()
        self.tab_widget.addTab(self.trading_tab, "Торговля")

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Готов к работе")

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._start_load_markets)

    @asyncSlot()
    async def _start_load_markets(self):
        await self.load_all_markets()

    def create_trading_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        exchange_group = QGroupBox("Выбор биржи и настройки")
        exchange_layout = QGridLayout(exchange_group)

        exchange_layout.addWidget(QLabel("Биржа:"), 0, 0)
        self.exchange_combo = QComboBox()
        self.exchange_combo.addItems(list(self.get_supported_exchanges().keys()))
        exchange_layout.addWidget(self.exchange_combo, 0, 1)

        exchange_layout.addWidget(QLabel("Символ:"), 1, 0)
        self.symbol_combo = QComboBox()
        self.symbol_combo.setEditable(True)
        exchange_layout.addWidget(self.symbol_combo, 1, 1)

        exchange_layout.addWidget(QLabel("Цена продажи:"), 2, 0)
        self.price_edit = QLineEdit()
        exchange_layout.addWidget(self.price_edit, 2, 1)

        buttons_layout = QHBoxLayout()
        
        self.create_order_btn = QPushButton("Создать ордер")
        self.cancel_task_btn = QPushButton("Отменить задачу")
        self.resume_task_btn = QPushButton("Возобновить")
        self.edit_price_btn = QPushButton("Изменить цену")
        self.delete_task_btn = QPushButton("Удалить задачу")

        buttons_layout.addWidget(self.create_order_btn)
        buttons_layout.addWidget(self.cancel_task_btn)
        buttons_layout.addWidget(self.resume_task_btn)
        buttons_layout.addWidget(self.edit_price_btn)
        buttons_layout.addWidget(self.delete_task_btn)
        exchange_layout.addLayout(buttons_layout, 3, 0, 1, 2)
        layout.addWidget(exchange_group)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tasks_table = QTableWidget()
        self.setup_tasks_table()
        splitter.addWidget(self.tasks_table)

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.addWidget(QLabel("Логи:"))
        
        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(300)
        log_layout.addWidget(self.log_text)
        
        splitter.addWidget(log_widget)
        splitter.setSizes([800, 400])

        layout.addWidget(splitter)
        return tab

    def setup_tasks_table(self):
        self.tasks_table.setColumnCount(5)
        headers = ["Биржа", "Символ", "Цена", "В ордере", "Статус"]
        self.tasks_table.setHorizontalHeaderLabels(headers)
        
        header = self.tasks_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

    def setup_connections(self):
        self.exchange_combo.currentTextChanged.connect(self.on_exchange_changed)
        self.symbol_combo.currentTextChanged.connect(self.on_symbol_changed)
        
        self.create_order_btn.clicked.connect(self.create_order)
        self.cancel_task_btn.clicked.connect(self.cancel_task)
        self.resume_task_btn.clicked.connect(self.resume_task)
        self.edit_price_btn.clicked.connect(self.edit_price)
        self.delete_task_btn.clicked.connect(self.delete_task)

        self.task_manager.task_updated.connect(self.update_task_in_table)
        self.task_manager.log_message.connect(self.add_log_message)

    def setup_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QWidget {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QGroupBox {
                border: 2px solid #555555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QPushButton {
                background-color: #0078d4;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QPushButton:pressed {
                background-color: #005a9e;
            }
            QLineEdit, QComboBox {
                padding: 5px;
                border: 1px solid #555555;
                border-radius: 3px;
                background-color: #3b3b3b;
            }
            QTableWidget {
                gridline-color: #555555;
                background-color: #3b3b3b;
                alternate-background-color: #404040;
            }
            QHeaderView::section {
                background-color: #0078d4;
                padding: 4px;
                border: 1px solid #555555;
                font-weight: bold;
            }
            QTextEdit {
                background-color: #3b3b3b;
                border: 1px solid #555555;
                border-radius: 3px;
            }
            QTabWidget::pane {
                border: 1px solid #555555;
            }
            QTabBar::tab {
                background-color: #404040;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #0078d4;
            }
        """)

    def load_config(self):
        try:
            config_path = os.path.join(os.path.dirname(__file__), "api_keys.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    self.config = json.load(f)
            else:
                self.config = {}
                self.add_log_message("Файл api_keys.json не найден!")
        except Exception as e:
            self.config = {}
            self.add_log_message(f"Ошибка загрузки конфигурации: {e}")

    def get_supported_exchanges(self):
        return {
            "Binance": ccxt.binance,
            "Bitget": ccxt.bitget,
            "Bybit": ccxt.bybit,
            "Gate": ccxt.gateio,
            "Huobi": ccxt.huobi,
            "KuCoin": ccxt.kucoin,
            "MEXC": ccxt.mexc,
            "OKX": ccxt.okx,
            "Bitmart": ccxt.bitmart,
            "Poloniex": ccxt.poloniex,
            "Coinex": ccxt.coinex,
            "BingX": ccxt.bingx,
            "XT": ccxt.xt,
        }

    async def load_all_markets(self):
        tasks = []
        for exchange_name in self.get_supported_exchanges().keys():
            keys = self.config.get(exchange_name.lower() + "_keys", {})
            if keys:
                exchange_class = self.get_supported_exchanges()[exchange_name]
                proxy = self.config.get("proxy_keys")
                tasks.append(self.task_manager.load_exchange_markets(exchange_name, exchange_class, keys, proxy))
        if tasks:
            await asyncio.gather(*tasks)

    def on_exchange_changed(self):
        exchange_name = self.exchange_combo.currentText()
        if exchange_name in self.task_manager.loaded_markets:
            symbols = list(self.task_manager.loaded_markets[exchange_name].keys())
            self.symbol_combo.clear()
            self.symbol_combo.addItems(symbols)

    def on_symbol_changed(self):
        asyncio.create_task(self.update_current_price())

    async def update_current_price(self):
        exchange_name = self.exchange_combo.currentText()
        symbol = self.symbol_combo.currentText().upper()
        
        if exchange_name and symbol:
            keys = self.config.get(exchange_name.lower() + "_keys", {})
            if keys:
                exchange_class = self.get_supported_exchanges()[exchange_name]
                proxy = self.config.get("proxy_keys")
                
                price = await self.task_manager.get_current_price(
                    exchange_name, exchange_class, keys, proxy, symbol
                )
                
                if price:
                    price_str = f"{price:.10f}".rstrip('0').rstrip('.')
                    self.price_edit.setText(price_str)

    def create_order(self):
        exchange_name = self.exchange_combo.currentText()
        symbol = self.symbol_combo.currentText().upper()
        price_str = self.price_edit.text()

        if not all([exchange_name, symbol, price_str]):
            QMessageBox.warning(self, "Ошибка", "Заполните все поля!")
            return

        try:
            price = float(price_str)
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Неверный формат цены!")
            return

        keys = self.config.get(exchange_name.lower() + "_keys", {})
        if not keys:
            QMessageBox.warning(self, "Ошибка", f"Ключи для {exchange_name} не найдены!")
            return

        if exchange_name not in self.task_manager.loaded_markets:
            QMessageBox.warning(self, "Ошибка", f"Маркеты для {exchange_name} не загружены!")
            return

        if symbol not in self.task_manager.loaded_markets[exchange_name]:
            QMessageBox.warning(self, "Ошибка", f"Символ {symbol} не найден на бирже {exchange_name}!")
            return

        exchange_class = self.get_supported_exchanges()[exchange_name]
        proxy = self.config.get("proxy_keys")

        task_id = str(len(self.task_manager.tasks) + 1)
        self.task_manager.tasks[task_id] = {
            "exchange_key": exchange_name,
            "symbol": symbol,
            "price": price,
            "exchange_class": exchange_class,
            "keys": keys,
            "proxy": proxy,
            "order_id": None
        }

        self.add_task_to_table(task_id, exchange_name, symbol, f"{price:.10f}".rstrip('0').rstrip('.'), 0, "Запуск...")

        task = asyncio.create_task(
            self.task_manager.fetch_balance_and_sell_loop(
                task_id, exchange_name, exchange_class, keys, proxy, symbol
            )
        )
        self.task_manager.tasks[task_id]["task"] = task

    def add_task_to_table(self, task_id, exchange, symbol, price, in_order, status):
        row = self.tasks_table.rowCount()
        self.tasks_table.insertRow(row)
        
        self.tasks_table.setItem(row, 0, QTableWidgetItem(exchange))
        self.tasks_table.setItem(row, 1, QTableWidgetItem(symbol))
        self.tasks_table.setItem(row, 2, QTableWidgetItem(price))
        self.tasks_table.setItem(row, 3, QTableWidgetItem(str(in_order)))
        self.tasks_table.setItem(row, 4, QTableWidgetItem(status))
        
        self.tasks_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, task_id)

    def update_task_in_table(self, task_id, task_data):
        for row in range(self.tasks_table.rowCount()):
            item = self.tasks_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == task_id:
                self.tasks_table.setItem(row, 0, QTableWidgetItem(task_data['exchange']))
                self.tasks_table.setItem(row, 1, QTableWidgetItem(task_data['symbol']))
                self.tasks_table.setItem(row, 2, QTableWidgetItem(task_data['price']))
                self.tasks_table.setItem(row, 3, QTableWidgetItem(str(task_data['in_order'])))
                self.tasks_table.setItem(row, 4, QTableWidgetItem(task_data['status']))
                self.tasks_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, task_id)
                break

    def get_selected_task_id(self):
        current_row = self.tasks_table.currentRow()
        if current_row >= 0:
            item = self.tasks_table.item(current_row, 0)
            if item:
                return item.data(Qt.ItemDataRole.UserRole)
        return None

    def cancel_task(self):
        task_id = self.get_selected_task_id()
        if not task_id:
            QMessageBox.warning(self, "Ошибка", "Выберите задачу для отмены!")
            return

        if task_id in self.task_manager.tasks and "task" in self.task_manager.tasks[task_id]:
            task = self.task_manager.tasks[task_id]["task"]
            if not task.done():
                task.cancel()
            
            asyncio.create_task(self._cancel_active_order(task_id))
            self.add_log_message(f"Задача {task_id} отменена")

    def resume_task(self):
        task_id = self.get_selected_task_id()
        if not task_id:
            QMessageBox.warning(self, "Ошибка", "Выберите задачу для возобновления!")
            return

        if task_id not in self.task_manager.tasks:
            return

        task_data = self.task_manager.tasks[task_id]
        if "task" in task_data and not task_data["task"].done():
            self.add_log_message(f"Задача {task_id} уже выполняется")
            return

        new_task = asyncio.create_task(
            self.task_manager.fetch_balance_and_sell_loop(
                task_id, task_data["exchange_key"], task_data["exchange_class"],
                task_data["keys"], task_data["proxy"], task_data["symbol"]
            )
        )
        self.task_manager.tasks[task_id]["task"] = new_task
        self.add_log_message(f"Задача {task_id} возобновлена")

    def edit_price(self):
        task_id = self.get_selected_task_id()
        if not task_id:
            QMessageBox.warning(self, "Ошибка", "Выберите задачу для редактирования!")
            return

        current_price = self.task_manager.tasks[task_id]["price"]
        new_price, ok = QInputDialog.getDouble(
            self, "Изменить цену", "Новая цена:", current_price, decimals=10
        )

        if ok:
            self.task_manager.tasks[task_id]["price"] = new_price
            asyncio.create_task(self._update_active_order_price(task_id, new_price))
            self.add_log_message(f"Цена задачи {task_id} изменена на {new_price}")

    def delete_task(self):
        task_id = self.get_selected_task_id()
        if not task_id:
            QMessageBox.warning(self, "Ошибка", "Выберите задачу для удаления!")
            return

        reply = QMessageBox.question(
            self, "Подтверждение", "Удалить выбранную задачу?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            if task_id in self.task_manager.tasks:
                if "task" in self.task_manager.tasks[task_id]:
                    task = self.task_manager.tasks[task_id]["task"]
                    if not task.done():
                        task.cancel()
                
                asyncio.create_task(self._cancel_active_order(task_id))
                del self.task_manager.tasks[task_id]

            current_row = self.tasks_table.currentRow()
            if current_row >= 0:
                self.tasks_table.removeRow(current_row)

            self.add_log_message(f"Задача {task_id} удалена")

    async def _cancel_active_order(self, task_id):
        if task_id not in self.task_manager.tasks:
            return

        task_data = self.task_manager.tasks[task_id]
        order_id = task_data.get("order_id")
        
        if not order_id:
            return

        try:
            exchange_class = task_data["exchange_class"]
            keys = task_data["keys"]
            proxy = task_data["proxy"]
            symbol = task_data["symbol"]

            async with BaseExchange(exchange_class, keys, proxy) as current_exchange:
                await current_exchange.exchange.cancel_order(order_id, symbol)
                self.add_log_message(f"Ордер {order_id} отменен для задачи {task_id}")
                task_data["order_id"] = None
        except Exception as e:
            self.add_log_message(f"Ошибка отмены ордера {order_id}: {e}")

    async def _update_active_order_price(self, task_id, new_price):
        if task_id not in self.task_manager.tasks:
            return

        task_data = self.task_manager.tasks[task_id]
        order_id = task_data.get("order_id")
        
        if not order_id:
            return

        try:
            exchange_class = task_data["exchange_class"]
            keys = task_data["keys"]
            proxy = task_data["proxy"]
            symbol = task_data["symbol"]

            async with BaseExchange(exchange_class, keys, proxy) as current_exchange:
                order = await current_exchange.exchange.fetch_order(order_id, symbol)
                if order and order.get('remaining', 0) > 0:
                    await current_exchange.exchange.cancel_order(order_id, symbol)
                    self.add_log_message(f"Старый ордер {order_id} отменен")
                    
                    remaining_amount = order.get('remaining', 0)
                    if remaining_amount > 0:
                        price_str = f"{new_price:.10f}".rstrip('0').rstrip('.')
                        new_order = await current_exchange.exchange.create_limit_sell_order(
                            symbol, remaining_amount, price_str
                        )
                        task_data["order_id"] = new_order.get('id')
                        self.add_log_message(f"Создан новый ордер {new_order.get('id')} с ценой {new_price}")
                    else:
                        task_data["order_id"] = None
                else:
                    task_data["order_id"] = None
        except Exception as e:
            self.add_log_message(f"Ошибка изменения цены ордера: {e}")

    def add_log_message(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        self.log_text.append(formatted_message)
        
        if self.log_text.document().blockCount() > 1000:
            cursor = self.log_text.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(cursor.MoveOperation.Down, cursor.MoveMode.KeepAnchor, 100)
            cursor.removeSelectedText()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    window = SellerMainWindow()
    window.show()

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
