#!/usr/bin/env ruby

def smart_split(line)
  return nil if line.nil? || line.strip.empty?
  line = line.gsub(/([¥$€])\s+/, '\1')
  parts = line.strip.split
  parts.empty? ? nil : parts
end

def get_ocr_text(image_path)
  cmd = "python3 -c \"from PIL import Image; import pytesseract; print(repr(pytesseract.image_to_string(Image.open('#{image_path}'))))\""
  result = `#{cmd}`
  eval(result) rescue result
end

def process_folder(folder_path)
  puts "\nProcessing: #{File.basename(folder_path)}"

  market_img = File.join(folder_path, "market_report.png")
  return puts("  No market_report.png") unless File.exist?(market_img)

  text = get_ocr_text(market_img)
  lines = text.split("\n")

  cities = []
  current_city = nil
  start_idx = 0

  lines.each_with_index do |line, i|
    if line.include?("Market Report - ")
      cities << [current_city, start_idx, i] if current_city
      current_city = line.gsub("Market Report - ", "").strip
      start_idx = i
    end
  end
  cities << [current_city, start_idx, lines.size] if current_city
  cities = [["Market", 0, lines.size]] if cities.empty?

  puts "  Found #{cities.size} cities"

  # Use Python + openpyxl since caxlsx requires more setup
  # Generate Python script on the fly
  cities.each do |city_name, start, end_idx|
    next unless city_name

    safe_name = city_name.gsub(/[^\w\s-]/, '').gsub(' ', '_')
    folder_name = File.basename(folder_path)
    filename = "#{folder_name}_market_#{safe_name}_axlsx.xlsx"
    filepath = File.join(folder_path, filename)

    # Write Python script
    py_script = <<~PYTHON
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

lines = #{lines[start...end_idx].inspect}

wb = Workbook()
ws = wb.active
ws.title = "Market Report"

header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
header_font = Font(bold=True, color="FFFFFF")

rows_written = 0

for line in lines:
    if not line or not line.strip():
        continue
    line = line.strip()
    # Keep currency with numbers
    import re
    line = re.sub(r'([¥$€])\\s+', r'\\1', line)
    parts = line.split()

    garbage_count = sum(1 for c in ''.join(parts) if c in '|#@[]{}')
    if garbage_count > 3:
        continue

    ws.append(parts)
    rows_written += 1

    is_header = ("Population" in line and "Penetration" in line) or \\
                 ("Team" in line and ("Management" in line or "Index" in line))
    if is_header:
        for cell in ws[ws.max_row]:
            cell.fill = header_fill
            cell.font = header_font

for column in ws.columns:
    max_length = 0
    column_letter = column[0].column_letter
    for cell in column:
        try:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        except:
            pass
    adjusted_width = min(max_length + 2, 50)
    ws.column_dimensions[column_letter].width = adjusted_width

import os
filepath = #{filepath.inspect}
if os.path.exists(filepath):
    try:
        os.remove(filepath)
    except:
        import time
        base, ext = os.path.splitext(filepath)
        filepath = f"{base}_v2{ext}"

wb.save(filepath)
print(f"    Saved: {os.path.basename(filepath)} ({rows_written} rows)")
PYTHON

    # Run Python script
    temp_py = "/tmp/temp_market_script.py"
    File.write(temp_py, py_script)
    system("python3 #{temp_py}")
    File.delete(temp_py) if File.exist?(temp_py)
  end
end

def main
  base_dir = "/mnt/c/Users/david/documents/ASDAN/表格/WYEF"
  folders = ["r-1", "r1", "r2", "r3", "r4", "r5", "r6", "r7"]

  folders.each do |f|
    folder_path = File.join(base_dir, f)
    process_folder(folder_path) if File.directory?(folder_path)
  end
end

main if __FILE__ == $PROGRAM_NAME
