// The texts written in index.html, marked here so that xgettext finds them.
// Elements carry them in data-text (their content) or data-label (aria-label).

export const N_ = (text) => text;

export const PAGE_TEXTS = [
  N_("Photos"),
  N_("Choose a folder…"),
  N_("Track"),
  N_("Choose a GPX file…"),
  // Translators: a button that turns on moving photos on the map by hand
  N_("Edit locations"),
  N_("Options"),
  N_("Overwrite existing locations"),
  N_("Photos that already have a location also get one from the track. Their existing location will be overwritten."),
  N_("Stops"),
  N_("Photos taken while standing still get the steadier position of the stop."),
  N_("Choose a folder with photos and a track to start."),
  N_("A GPX file can also be dropped anywhere on this page."),
  // Translators: the label of the slider that corrects the camera clock
  N_("Time correction"),
  // Translators: a button that moves the time correction back by an hour
  N_("−1 h"),
  // Translators: a button that moves the time correction on by an hour
  N_("+1 h"),
  N_("Reset"),
  N_("Close"),
  N_("Folder or file"),
  N_("Open"),
  N_("Include subfolders"),
  N_("Cancel"),
  N_("Drop the GPX file to use it as the track"),
];
