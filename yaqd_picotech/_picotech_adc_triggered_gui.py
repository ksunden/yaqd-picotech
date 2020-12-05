"""Qt based GUI client for ni-daqmx-tmux."""

import sys
import pathlib

from qtpy import QtCore, QtGui, QtWidgets  # type: ignore
import pyqtgraph as pg  # type: ignore
import qtypes  # type: ignore
import yaqc  # type: ignore
import toml
import numpy as np  # type: ignore
import time

ranges = [0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20]


class Channel:
    def __init__(
        self,
        nsamples,
        # label,
        range,
        signal_start,
        signal_stop,
        processing_method,
        enabled,
        coupling,
        invert,
        use_baseline,
        baseline_start,
        baseline_stop,
    ):
        print(signal_start, signal_stop, range, nsamples)

        self.enabled = qtypes.Bool(value=enabled)
        # self.label = qtypes.String(value=label)
        self.physical_correspondance = qtypes.Number(
            decimals=0, limits=qtypes.NumberLimits(0, 4, None)
        )
        allowed_ranges = ["{0:0.2f}".format(r) for r in ranges]
        range_id = [i for i, a in enumerate(ranges) if a == float(range[:-2])][0]
        self.range = qtypes.Enum(allowed_values=allowed_ranges, initial_value=allowed_ranges[range_id])
        self.invert = qtypes.Bool(value=invert)
        sample_limits = qtypes.NumberLimits(0, nsamples - 1, None)
        self.signal_start_index = qtypes.Number(
            decimals=0, limits=sample_limits, value=signal_start
        )
        self.signal_stop_index = qtypes.Number(decimals=0, limits=sample_limits, value=signal_stop)
        processing_methods = ["average", "sum", "min", "max"]  # TODO: source from avpr
        self.processing_method = qtypes.Enum(allowed_values=processing_methods, value=processing_method)
        self.use_baseline = qtypes.Bool(value=use_baseline)
        self.baseline_start_index = qtypes.Number(
            decimals=0, limits=sample_limits, value=baseline_start if baseline_start is not None else 0
        )
        self.baseline_stop_index = qtypes.Number(
            decimals=0, limits=sample_limits, value=baseline_stop if baseline_stop is not None else 0
        )
        # signals
        self.use_baseline.updated.connect(lambda: self.on_use_baseline())
        self.on_use_baseline()

    @property
    def baseline_start(self):
        return self.baseline_start_index.get()

    @property
    def baseline_stop(self):
        return self.baseline_stop_index.get()

    def get_range(self):
        """
        Returns
        -------
        tuple
            (minimum_voltage, maximum_voltage)
        """
        r = ranges[self.range.get_index()]
        return -r, r

    def get_widget(self):
        self.input_table = qtypes.widgets.InputTable()
        # self.input_table.append(self.label, "Label")
        self.input_table.append(self.range, "Range +/-V")
        self.input_table.append(self.signal_start_index, "Signal Start")
        self.input_table.append(self.signal_stop_index, "Signal Stop")
        self.input_table.append(self.invert, "Invert")
        self.input_table.append(self.processing_method, "Method")
        self.input_table.append(self.use_baseline, "Use Baseline")
        self.input_table.append(self.baseline_start_index, "Baseline Start")
        self.input_table.append(self.baseline_stop_index, "Baseline Stop")
        return self.input_table

    def on_use_baseline(self):
        self.processing_method.set_disabled(not self.use_baseline.get())
        self.baseline_start_index.set_disabled(not self.use_baseline.get())
        self.baseline_stop_index.set_disabled(not self.use_baseline.get())

    @property
    def signal_start(self):
        return self.signal_start_index.get()

    @property
    def signal_stop(self):
        return self.signal_stop_index.get()


class ConfigWidget(QtWidgets.QWidget):
    def __init__(self, port):
        super().__init__()
        self.port = port
        self.client = yaqc.Client(self.port)
        self.client.measure(loop=True)
        config = toml.loads(self.client.get_config())
        self.nsamples = config["max_samples"]
        self.channels = {}
        for name, d in config["channels"].items():
            self.channels[name] = Channel(**d, nsamples=self.nsamples)
        self.create_frame()
        self.poll_timer = QtCore.QTimer()
        self.poll_timer.start(100)  # milliseconds
        self.poll_timer.timeout.connect(self.update)

    def create_frame(self):
        self.setLayout(QtWidgets.QHBoxLayout())
        self.layout().setContentsMargins(0, 10, 0, 0)
        self.tabs = QtWidgets.QTabWidget()
        # samples tab
        samples_widget = QtWidgets.QWidget()
        samples_box = QtWidgets.QHBoxLayout()
        samples_box.setContentsMargins(0, 10, 0, 0)
        samples_widget.setLayout(samples_box)
        self.tabs.addTab(samples_widget, "Samples")
        self.create_samples_tab(samples_box)
        # shots tab
        shots_widget = QtWidgets.QWidget()
        shots_box = QtWidgets.QHBoxLayout()
        shots_box.setContentsMargins(0, 10, 0, 0)
        shots_widget.setLayout(shots_box)
        self.tabs.addTab(shots_widget, "Shots")
        self.create_shots_tab(shots_box)
        # finish
        self.layout().addWidget(self.tabs)
        self.update_samples_tab()

    def create_samples_tab(self, layout):
        # container widget
        display_container_widget = QtWidgets.QWidget()
        display_container_widget.setLayout(QtWidgets.QVBoxLayout())
        display_layout = display_container_widget.layout()
        layout.addWidget(display_container_widget)
        # plot
        self.samples_plot_widget = Plot1D(yAutoRange=False)
        self.samples_plot_scatter = self.samples_plot_widget.add_scatter(color=0.25)
        self.samples_plot_active_scatter = self.samples_plot_widget.add_scatter()
        self.samples_plot_widget.set_labels(xlabel="sample", ylabel="volts")
        self.samples_plot_max_voltage_line = self.samples_plot_widget.add_infinite_line(
            color="y", angle=0
        )
        self.samples_plot_min_voltage_line = self.samples_plot_widget.add_infinite_line(
            color="y", angle=0
        )
        self.samples_plot_signal_stop_line = self.samples_plot_widget.add_infinite_line(color="r")
        self.samples_plot_signal_start_line = self.samples_plot_widget.add_infinite_line(color="g")
        self.samples_plot_baseline_stop_line = self.samples_plot_widget.add_infinite_line(
            color="r", style="dashed"
        )
        self.samples_plot_baseline_start_line = self.samples_plot_widget.add_infinite_line(
            color="g", style="dashed"
        )
        display_layout.addWidget(self.samples_plot_widget)
        legend = self.samples_plot_widget.plot_object.addLegend()
        legend.addItem(self.samples_plot_active_scatter, "channel samples")
        legend.addItem(self.samples_plot_scatter, "other samples")
        style = pg.PlotDataItem(pen="y")
        legend.addItem(style, "voltage limits")
        style = pg.PlotDataItem(pen="g")
        legend.addItem(style, "signal start")
        style = pg.PlotDataItem(pen="r")
        legend.addItem(style, "signal stop")
        pen = pg.mkPen("g", style=QtCore.Qt.DashLine)
        style = pg.PlotDataItem(pen=pen)
        legend.addItem(style, "baseline start")
        pen = pg.mkPen("r", style=QtCore.Qt.DashLine)
        style = pg.PlotDataItem(pen=pen)
        legend.addItem(style, "baseline stop")
        style = pg.PlotDataItem(pen="b")
        # vertical line -------------------------------------------------------
        line = qtypes.widgets.Line("V")
        layout.addWidget(line)
        # settings area -------------------------------------------------------
        # container widget / scroll area
        settings_container_widget = QtWidgets.QWidget()
        settings_scroll_area = qtypes.widgets.ScrollArea()
        settings_scroll_area.setWidget(settings_container_widget)
        settings_container_widget.setLayout(QtWidgets.QVBoxLayout())
        settings_layout = settings_container_widget.layout()
        settings_layout.setContentsMargins(5, 5, 5, 5)
        layout.addWidget(settings_scroll_area)
        input_table = qtypes.widgets.InputTable()
        input_table.append(None, "Settings")
        settings_layout.addWidget(input_table)
        # channels
        line = qtypes.widgets.Line("H")
        settings_layout.addWidget(line)
        # channel_combobox
        allowed_values = list(self.channels.keys())
        self.samples_channel_combo = qtypes.Enum(allowed_values=allowed_values, name="Channels")
        input_table = qtypes.widgets.InputTable()
        input_table.append(self.samples_channel_combo)
        settings_layout.addWidget(input_table)
        # channel widgets
        self.channel_widgets = []
        for channel in self.channels.values():
            widget = channel.get_widget()
            settings_layout.addWidget(widget)
            # widget.hide()
            self.channel_widgets.append(widget)
        # apply button
        self.apply_channel_button = qtypes.widgets.PushButton("APPLY CHANGES", background="green")
        self.apply_channel_button.clicked.connect(self.write_config)
        settings_layout.addWidget(self.apply_channel_button)
        # dividing line
        line = qtypes.widgets.Line("H")
        settings_layout.addWidget(line)
        # finish --------------------------------------------------------------
        settings_layout.addStretch(1)
        self.sample_xi = np.arange(self.nsamples)

    def create_shots_tab(self, layout):
        # container widget
        display_container_widget = QtWidgets.QWidget()
        display_container_widget.setLayout(QtWidgets.QVBoxLayout())
        display_layout = display_container_widget.layout()
        display_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(display_container_widget)
        # plot
        self.shots_plot_widget = Plot1D()
        self.shots_plot_scatter = self.shots_plot_widget.add_scatter()
        self.shots_plot_widget.set_labels(xlabel="shot", ylabel="volts")
        display_layout.addWidget(self.shots_plot_widget)
        # vertical line
        line = qtypes.widgets.Line("V")
        layout.addWidget(line)
        # settings
        # container widget / scroll area
        settings_container_widget = QtWidgets.QWidget()
        settings_scroll_area = qtypes.widgets.ScrollArea()
        settings_scroll_area.setWidget(settings_container_widget)
        settings_container_widget.setLayout(QtWidgets.QVBoxLayout())
        settings_layout = settings_container_widget.layout()
        settings_layout.setContentsMargins(5, 5, 5, 5)
        layout.addWidget(settings_scroll_area)
        # input table
        input_table = qtypes.widgets.InputTable()
        input_table.append(None, "Display")
        self.shot_channel_combo = qtypes.Enum(name="Channel")
        input_table.append(self.shot_channel_combo)
        self.shot_channel_combo.updated.connect(self.on_shot_channel_updated)
        input_table.append(None, "Settings")
        self.nshots = qtypes.Number(name="Shots", value=self.client.get_nshots(), decimals=0)
        self.nshots.updated.connect(self.on_nshots_updated)
        input_table.append(self.nshots)
        self.shots_processing_module_path = qtypes.Filepath(name="Shots Processing")
        input_table.append(self.shots_processing_module_path)
        settings_layout.addWidget(input_table)
        # finish
        settings_layout.addStretch(1)
        self.shot_channel_combo.updated.emit()

    def write_config(self):
        # create dictionary, starting from existing
        config = toml.loads(self.client.get_config())
        # channels
        for k in config["channels"].keys():
            channel = self.channels[k]
            # config["channels"][k]["label"] = channel.label.get()
            config["channels"][k]["range"] = channel.range.get()
            config["channels"][k]["enabled"] = channel.enabled.get()
            config["channels"][k]["invert"] = channel.invert.get()
            config["channels"][k]["signal_start"] = channel.signal_start_index.get()
            config["channels"][k]["signal_stop"] = channel.signal_stop_index.get()
            config["channels"][k]["processing_method"] = channel.processing_method.get()
            config["channels"][k]["use_baseline"] = channel.use_baseline.get()
            config["channels"][k]["baseline_start"] = channel.baseline_start_index.get()
            config["channels"][k]["baseline_stop"] = channel.baseline_stop_index.get()
        print(toml.dumps({self.client.id()["name"]: config}))
        # ddk: prevent writing until fields are properly tested
        print("writing cancelled")
        return
        # write config
        with open(self.client.get_config_filepath(), 'w') as f:
            toml.dump(config, f)
        self.client.shutdown(restart=True)
        while True:
            try:
                self.client = yaqc.Client(self.port)
            except:
                time.sleep(0.1)

    def on_nshots_updated(self):
        new = int(self.nshots.get())
        self.client.set_nshots(new)
        self.nshots.set(self.client.get_nshots())  # read back

    def on_shot_channel_updated(self):
        # update y range to be range of channel
        channel_index = self.shot_channel_combo.get_index()
        active_channels = [channel for channel in self.channels.values() if channel.enabled.get()]
        if channel_index > len(active_channels) - 1:
            # must be a chopper
            ymin = -1
            ymax = 1
        else:
            # is a channel
            channel = active_channels[channel_index]
            ymin, ymax = channel.get_range()
        self.shots_plot_widget.set_ylim(ymin * 1.05, ymax * 1.05)

    def set_slice_xlim(self, xmin, xmax):
        self.values_plot_widget.set_xlim(xmin, xmax)

    def update(self):
        """
        samples:  (channel, shot, sample)
        shots: (channel, shot)
        """
        # sample from first shot
        yi = self.client.get_measured_samples()[int(self.shot_channel_combo.get_index())][0]
        self.samples_plot_scatter.clear()
        self.samples_plot_scatter.setData(self.sample_xi, yi)
        # active samples
        self.samples_plot_active_scatter.hide()
        current_channel_object = list(self.channels.values())[
            self.samples_channel_combo.get_index()
        ]
        # ddk: samples_plot_active might be a special feature for highlighting which samples make up an output channel (e.g. w2_diff)
        # ddk: this plot gives brighter colors and easier to see; use it!
        if current_channel_object.enabled.get():
            self.samples_plot_active_scatter.show()
            # s = slice(current_channel_object.signal_start, current_channel_object.signal_stop, 1)
            xi = self.sample_xi  # [s]
            """
            if current_channel_object.use_baseline.get():
                s = slice(
                    current_channel_object.baseline_start, current_channel_object.baseline_stop, 1
                )
                xi = np.hstack([xi, self.sample_xi[s]])
                yyi = np.hstack([yyi, yi[s]])
            """
            self.samples_plot_active_scatter.setData(xi, yi)
        # shots
        yi = self.client.get_measured_shots()[int(self.shot_channel_combo.get_index())]
        xi = np.arange(len(yi))
        self.shots_plot_scatter.clear()
        self.shots_plot_scatter.setData(xi, yi)

    def update_samples_tab(self):
        # buttons
        num_channels = len(self.samples_channel_combo.allowed_values)
        # channel ui
        channel_index = self.samples_channel_combo.get_index()
        for widget in self.channel_widgets:
            widget.hide()
        self.channel_widgets[channel_index].show()
        # lines on plot
        self.samples_plot_max_voltage_line.hide()
        self.samples_plot_min_voltage_line.hide()
        self.samples_plot_signal_start_line.hide()
        self.samples_plot_signal_stop_line.hide()
        self.samples_plot_baseline_start_line.hide()
        self.samples_plot_baseline_stop_line.hide()
        current_channel_object = list(self.channels.values())[channel_index]
        if current_channel_object.enabled.get():
            channel_min, channel_max = current_channel_object.get_range()
            self.samples_plot_max_voltage_line.show()
            self.samples_plot_max_voltage_line.setValue(channel_max * 1.05)
            self.samples_plot_min_voltage_line.show()
            self.samples_plot_min_voltage_line.setValue(channel_min * 1.05)
            self.samples_plot_signal_start_line.show()
            self.samples_plot_signal_start_line.setValue(
                current_channel_object.signal_start_index.get()
            )
            self.samples_plot_signal_stop_line.show()
            self.samples_plot_signal_stop_line.setValue(
                current_channel_object.signal_stop_index.get()
            )
            if current_channel_object.use_baseline.get():
                self.samples_plot_baseline_start_line.show()
                self.samples_plot_baseline_start_line.setValue(
                    current_channel_object.baseline_start_index.get()
                )
                self.samples_plot_baseline_stop_line.show()
                self.samples_plot_baseline_stop_line.setValue(
                    current_channel_object.baseline_stop_index.get()
                )
        # finish
        ymin, ymax = current_channel_object.get_range()
        self.samples_plot_widget.set_ylim(ymin, ymax)


class Plot1D(pg.GraphicsView):
    def __init__(self, title=None, xAutoRange=True, yAutoRange=True):
        pg.GraphicsView.__init__(self)
        # create layout
        self.graphics_layout = pg.GraphicsLayout(border="w")
        self.setCentralItem(self.graphics_layout)
        self.graphics_layout.layout.setSpacing(0)
        self.graphics_layout.setContentsMargins(0.0, 0.0, 1.0, 1.0)
        # create plot object
        self.plot_object = self.graphics_layout.addPlot(0, 0)
        self.labelStyle = {"color": "#FFF", "font-size": "14px"}
        self.x_axis = self.plot_object.getAxis("bottom")
        self.x_axis.setLabel(**self.labelStyle)
        self.y_axis = self.plot_object.getAxis("left")
        self.y_axis.setLabel(**self.labelStyle)
        self.plot_object.showGrid(x=True, y=True, alpha=0.5)
        self.plot_object.setMouseEnabled(False, True)
        self.plot_object.enableAutoRange(x=xAutoRange, y=yAutoRange)
        # title
        if title:
            self.plot_object.setTitle(title)

    def add_scatter(self, color="c", size=3, symbol="o"):
        curve = pg.ScatterPlotItem(symbol=symbol, pen=(color), brush=(color), size=size)
        self.plot_object.addItem(curve)
        return curve

    def add_line(self, color="c", size=3, symbol="o"):
        curve = pg.PlotCurveItem(symbol=symbol, pen=(color), brush=(color), size=size)
        self.plot_object.addItem(curve)
        return curve

    def add_infinite_line(self, color="y", style="solid", angle=90.0, movable=False, hide=True):
        """
        Add an InfiniteLine object.
        Parameters
        ----------
        color : (optional)
            The color of the line. Accepts any argument valid for `pyqtgraph.mkColor <http://www.pyqtgraph.org/documentation/functions.html#pyqtgraph.mkColor>`_. Default is 'y', yellow.
        style : {'solid', 'dashed', dotted'} (optional)
            Linestyle. Default is solid.
        angle : float (optional)
            The angle of the line. 90 is vertical and 0 is horizontal. 90 is default.
        movable : bool (optional)
            Toggles if user can move the line. Default is False.
        hide : bool (optional)
            Toggles if the line is hidden upon initialization. Default is True.
        Returns
        -------
        InfiniteLine object
            Useful methods: setValue, show, hide
        """
        if style == "solid":
            linestyle = QtCore.Qt.SolidLine
        elif style == "dashed":
            linestyle = QtCore.Qt.DashLine
        elif style == "dotted":
            linestyle = QtCore.Qt.DotLine
        else:
            print("style not recognized in add_infinite_line")
            linestyle = QtCore.Qt.SolidLine
        pen = pg.mkPen(color, style=linestyle)
        line = pg.InfiniteLine(pen=pen)
        line.setAngle(angle)
        line.setMovable(movable)
        if hide:
            line.hide()
        self.plot_object.addItem(line)
        return line

    def set_labels(self, xlabel=None, ylabel=None):
        if xlabel:
            self.plot_object.setLabel("bottom", text=xlabel)
            self.plot_object.showLabel("bottom")
        if ylabel:
            self.plot_object.setLabel("left", text=ylabel)
            self.plot_object.showLabel("left")

    def set_xlim(self, xmin, xmax):
        self.plot_object.setXRange(xmin, xmax)

    def set_ylim(self, ymin, ymax):
        self.plot_object.setYRange(ymin, ymax)

    def clear(self):
        self.plot_object.clear()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, app, port):
        super().__init__()
        self.app = app
        self.setWindowTitle("Picoscope")
        self.setCentralWidget(ConfigWidget(port))


def main():
    """Initialize application and main window."""
    port = int(sys.argv[1])
    app = QtWidgets.QApplication(sys.argv)
    main_window = MainWindow(app, port)
    main_window.showMaximized()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()