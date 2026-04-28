#!/usr/bin/env ruby
require 'axlsx'
require 'pytesseract'  # You'll need to install this or use another OCR library
require 'rmagick'     # For image processing

def smart_split(line)
  return nil if line.nil? || line.strip.empty?

  # Keep currency symbols with numbers
  line = line.gsub(/([¥$€])\s+/, '\1')
  parts = line.strip.split
  parts.empty? ? nil : parts
end

def process_folder(folder_path)
  puts "\nProcessing: #{File.basename(folder_path)}"

  market_img = File.join(folder_path, "market_report.png")
  return puts("  No market_report.png") unless File.exist?(market_img)

  # OCR - you'll need to set up proper OCR for Ruby
  # For this example, we'll assume we have the text
  # In real use, you might use:
  # text = `python3 -c "from PIL import Image; import pytesseract; print(pytesseract.image_to_string(Image.open('#{market_img}')))"`
  text = `python3 -c "from PIL import Image; import pytesseract; print(pytesseract.image_to_string(Image.open('#{market_img}')))"`
  lines = text.split("\n")

  # Find city sections
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

  # Process each city with axlsx
  cities.each do |city_name, start, end_idx|
    Axlsx::Package.new do |p|
      wb = p.workbook

      # Create styles
      blue_cell = wb.styles.add_style(
        bg_color: "4472C4",
        fg_color: "FFFFFF",
        b: true,
        alignment: { horizontal: :center }
      )

      wb.add_worksheet(name: "Market Report") do |sheet|
        city_lines = lines[start...end_idx]
        rows_written = 0

        city_lines.each do |line|
          parts = smart_split(line)
          next unless parts

          # Skip garbage lines
          garbage_count = parts.join.count('|#@[]{}')
          next if garbage_count > 3

          # Determine if this is a header
          is_header = (line.include?("Population") && line.include?("Penetration")) ||
                      (line.include?("Team") && (line.include?("Management") || line.include?("Index")))

          style = is_header ? blue_cell : nil
          sheet.add_row parts, style: style
          rows_written += 1
        end

        # Auto fit columns
        sheet.column_widths *sheet.cols.map { |col|
          [col.cells.map { |c| c.to_s.size }.max + 2, 50].min
        }
      end

      # Save file
      safe_name = city_name.gsub(/[^\w\s-]/, '').gsub(' ', '_')
      folder_name = File.basename(folder_path)
      filename = "#{folder_name}_market_#{safe_name}.xlsx"
      filepath = File.join(folder_path, filename)

      # Try to save
      begin
        File.delete(filepath) if File.exist?(filepath)
        sleep 0.1
      rescue
        filename = "#{folder_name}_market_#{safe_name}_new.xlsx"
        filepath = File.join(folder_path, filename)
      end

      p.serialize(filepath)
      puts "    Saved: #{filename} (#{rows_written} rows)"
    end
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
