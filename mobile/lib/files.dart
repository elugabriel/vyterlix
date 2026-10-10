import 'package:file_picker/file_picker.dart';

/// A file the person chose on their phone.
class PickedFile {
  const PickedFile({required this.name, required this.bytes});

  final String name;
  final List<int> bytes;
}

/// Choosing a file is a thing the phone does, so the app asks through this (tests give it a made-up
/// file instead of opening the phone's file chooser).
abstract class FileChooser {
  /// A .csv or Excel .xlsx file, or null if the person backed out.
  Future<PickedFile?> chooseSpreadsheet();
}

class PhoneFileChooser implements FileChooser {
  const PhoneFileChooser();

  @override
  Future<PickedFile?> chooseSpreadsheet() async {
    final result = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['csv', 'xlsx'],
    );
    if (result.isEmpty) return null;
    final file = result.first;
    return PickedFile(name: file.name, bytes: await file.readAsBytes());
  }
}
